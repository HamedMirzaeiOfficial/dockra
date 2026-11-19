# فصل ۲ — صف کار روی Postgres

## هدف این فصل

بفهمید چطور با یک جدول معمولی دیتابیس یک صف کار مطمئن ساخته می‌شود: هیچ کاری دو بار اجرا نشود، دو کار یک اپ هم‌زمان اجرا نشوند، و اگر worker وسط کار مرد، سیستم گیر نکند.

## چرا Redis یا RabbitMQ نه؟

یک پلتفرم تک‌سروری از قبل Postgres دارد. اضافه کردن broker یعنی یک سرویس دیگر برای نصب، پایش و بکاپ. مهم‌تر از آن: وقتی Job در **همان تراکنش** تغییر وضعیت ساخته شود (مثلاً `Deployment` جدید)، هیچ‌وقت حالتی پیش نمی‌آید که ردیف ساخته شده ولی پیام صف گم شده باشد (یا برعکس). این مشکل معروف **dual write** است. هزینه: worker باید هر ثانیه poll کند، و توان عملیاتی محدود است؛ برای چند صد کار در ساعت کاملاً کافی است.

## مدل `Job`

`api/engine/models.py` را باز کنید. فیلدهای مهم:

| فیلد | کار |
| --- | --- |
| `kind` | deploy، rollback، backup، restore، … (کدام handler اجرا شود) |
| `queue` | `build`، `ops` یا `rpc` (کدام نوع worker برش دارد) |
| `target` | `app:<slug>` یا `db:<id>` — قفل منطقی |
| `status` | `queued` → `running` → `done`/`failed` |
| `heartbeat_at` | آخرین زمانی که worker گفت «هنوز زنده‌ام» |
| `cancel_requested` | کاربر لغو خواسته |

## برداشتن کار به‌صورت اتمی

`_claim` در `engine/runner.py`:

```python
busy = set(Job.objects.filter(status=RUNNING).exclude(target="").values_list("target", flat=True))
for job_id, target in rows.order_by("id").values_list("id", "target")[:200]:
    if target and target in busy:
        continue
    claimed = Job.objects.filter(pk=job_id, status=QUEUED).update(status=RUNNING, ...)
    if claimed:
        return job_id
```

نکتهٔ کلیدی خط `update` است: `UPDATE ... WHERE id=? AND status='queued'`. اگر دو worker هم‌زمان یک ردیف را ببینند، دیتابیس فقط به یکی از آن‌ها «یک ردیف تغییر کرد» برمی‌گرداند. دومی صفر می‌گیرد و می‌رود سراغ بعدی. این **compare-and-set** روی ردیف است و بدون هیچ قفل صریحی، اجرای دوباره را غیرممکن می‌کند.

ولی یک مشکل ظریف می‌ماند: دو builder هم‌زمان `busy` را بخوانند، هر دو ببینند `app:shop` آزاد است، و هر کدام **یک کار متفاوت** از همان اپ را بردارند. compare-and-set اینجا کمکی نمی‌کند چون ردیف‌ها فرق دارند. برای همین روی Postgres کل انتخاب زیر یک قفل کوتاه انجام می‌شود:

```python
with transaction.atomic():
    cursor.execute("SELECT pg_advisory_xact_lock(7302)")
    job_id = _claim(queues)
```

`pg_advisory_xact_lock` یک قفل با اسم دلخواه (عدد) است که تا پایان تراکنش می‌ماند. فقط بخش «انتخاب» را سریالی می‌کند، نه اجرای کار را.

## قفل روی کانتینرهای یک اپ

جدا از صف، `engine/locks.py::app_lock` با `pg_advisory_lock(7301, app_id)` تضمین می‌کند که هر کاری که کانتینرهای یک اپ را عوض می‌کند (build، sync تنظیمات، rollback) هم‌زمان با دیگری انجام نشود. بدون آن، یک build ممکن بود کانتینرهای تازه‌ای را که sync همان لحظه ساخته، به‌عنوان «نسخهٔ قدیمی» حذف کند.

## heartbeat و بازیابی

worker ممکن است وسط کار بمیرد (OOM، ری‌استارت سرور). آن وقت Job برای همیشه `running` می‌ماند و اپ برای همیشه «در حال استقرار» — و چون `target` مشغول است، هیچ استقرار دیگری هم شروع نمی‌شود. راه‌حل:

1. `_Heartbeat` (در `_execute`) یک thread است که هر ۱۵ ثانیه `heartbeat_at` را تازه می‌کند.
2. `recover_stale_jobs` هر ۳۰ ثانیه کارهایی را که بیش از `DOCKRA_JOB_STALE_SECONDS` خبری نداده‌اند `failed` می‌کند و اپ یا دیتابیس مربوط را آزاد می‌کند.

دقت کنید `recover_stale_jobs` هم از همان الگوی `filter(status=RUNNING).update(...)` استفاده می‌کند؛ پس اگر دو worker هم‌زمان اجرایش کنند، هر کار فقط یک بار «بازیابی» می‌شود. به این خاصیت **idempotency** می‌گویند: اجرای دوباره نتیجه را عوض نمی‌کند. تست `FailedJobRecoveryTests` در `engine/test_platform.py` دقیقاً همین را چک می‌کند.

## چرا استقرار خودکار retry نمی‌شود؟

بکاپ خودکار ناموفق بعد از یک ساعت دوباره امتحان می‌شود، ولی استقرار ناموفق نه. چرا؟ چون بکاپ معمولاً به‌خاطر شرایط گذرا شکست می‌خورد (دیسک پر، شبکه)، ولی build معمولاً به‌خاطر کد (خطای syntax، migration بد) و تکرارش فقط منابع را هدر می‌دهد. تصمیم retry همیشه به **نوع خطا** وابسته است، نه یک قانون کلی.

## تمرین

1. با `DOCKRA_EAGER=0` دو بار `POST /api/apps/shop/deploy` بزنید و در `python manage.py shell` جدول `Job` را ببینید. `run_worker --queues build` را اجرا کنید و ترتیب اجرا را دنبال کنید.
2. در `_claim` خط `if target and target in busy: continue` را موقتاً پاک کنید و تست `test_two_deploys_of_one_app_never_run_together` را اجرا کنید. چه می‌شود؟ در دنیای واقعی چه اتفاقی می‌افتاد؟
3. چرا `rpc` کارها `target` ندارند؟ اگر داشتند، کاربری که وسط یک build ده‌دقیقه‌ای در کنسول `ls` می‌زند چه تجربه‌ای داشت؟
4. فرض کنید `DOCKRA_JOB_STALE_SECONDS=10` باشد ولی heartbeat هر ۱۵ ثانیه. چه باگی پیش می‌آید؟
