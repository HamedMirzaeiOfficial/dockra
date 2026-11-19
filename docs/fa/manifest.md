# مانیفست `dockra.json`

[English](../manifest.md) · **فارسی**

این فایل را در ریشهٔ پروژه بگذارید. سرور `dockra.json` و در نبودش `liara.json` را می‌خواند؛ پس پروژه‌هایی که از آن قالب استفاده می‌کنند بدون تغییر کار می‌کنند. همهٔ فیلدها به‌جز `app` اختیاری‌اند و فایل در هر استقرار دوباره خوانده می‌شود.

```json
{
  "app": "shop",
  "platform": "django",
  "port": 8000,
  "release": "python manage.py migrate --noinput",
  "healthCheck": { "path": "/health/" },
  "django": { "pythonVersion": "3.12", "collectStatic": true },
  "packages": ["ffmpeg"],
  "disks": [{ "name": "media", "mountTo": "/app/media", "sizeGb": 5 }],
  "cron": ["*/10 * * * * python manage.py clearsessions"]
}
```

## فیلدهای عمومی

| فیلد | نوع | توضیح |
| --- | --- | --- |
| `app` | رشته | شناسهٔ برنامه: حروف کوچک انگلیسی، عدد و خط تیره، با حرف شروع شود، حداکثر ۴۰ نویسه |
| `name` | رشته | نام نمایشی وقتی `dockra deploy` برنامه را می‌سازد |
| `platform` | رشته | `django`، `node`، `nextjs`، `static` یا `docker` |
| `port` | عدد | پورتی که برنامه داخل کانتینر روی آن گوش می‌دهد. پیش‌فرض‌ها: django و docker 8000، node و nextjs 3000، static 80 |
| `git` | رشته | آدرس مخزن Git هنگام ساخت برنامه (برای استقرار با وب‌هوک) |
| `start` | رشته | فرمان شروع به‌جای پیش‌فرض پلتفرم |
| `release` | رشته یا فهرست | پیش از رسیدن ترافیک به نسخهٔ جدید یک بار اجرا می‌شود؛ شکستش استقرار را متوقف می‌کند و نسخهٔ فعلی می‌ماند. فهرست با `&&` به هم وصل می‌شود |
| `healthCheck` | `{"path": "/…"}` یا رشته | مسیر HTTP برای سنجش سلامت؛ هر پاسخ زیر 500 سالم حساب می‌شود. بدون آن، باز بودن پورت کافی است |
| `packages` | فهرست | بسته‌های apt برای ساخت Django (مثلاً `ffmpeg`، `libmagic1`) |
| `disks` | فهرست | `name` (حروف، عدد، `_.-`)، `mountTo` (مسیر داخل کانتینر)، `sizeGb` (پیش‌فرض ۱). حذف یک مورد هیچ‌وقت دیسک را پاک نمی‌کند |
| `cron` | فهرست | پنج بخش زمان‌بندی و بعد فرمان. اگر وجود داشته باشد، در هر استقرار جایگزین همهٔ کران‌جاب‌های برنامه می‌شود |

## Django

| فیلد | پیش‌فرض | توضیح |
| --- | --- | --- |
| `django.pythonVersion` (یا `pythonVersion`) | خودکار | `3.6` تا `3.14`؛ `3.10.4` هم پذیرفته و گرد می‌شود. ایمیج `python:3.X-slim-bookworm` (برای 3.6/3.7 `bullseye`). بدون آن: `runtime.txt`، بعد `.python-version`، بعد نسخهٔ Django که در `requirements.txt` ثابت شده (مثلاً `Django==2.2` → 3.9، `3.2` → 3.10، `4.2` → 3.12) و در آخر 3.12 |
| `django.collectStatic` | `true` | اجرای `collectstatic --noinput` پیش از Gunicorn. اگر تنظیم نشده باشد، شکستش برنامه را متوقف نمی‌کند؛ `false` خاموشش می‌کند. پلتفرم `STATIC_ROOT` و `MEDIA_ROOT` را زیر `STATIC_URL` و `MEDIA_URL` سرو می‌کند، صرف‌نظر از `DEBUG` و بدون نیاز به WhiteNoise در پروژه |
| `django.compileMessages` | `false` | اجرای `compilemessages` پیش از شروع |

قالب یک venv با `requirements.txt` و Gunicorn می‌سازد، با کاربر غیر root (uid 1000) اجرا می‌شود و `HEALTHCHECK` اضافه می‌کند. Gunicorn از `wsgi.py` پروژه (یا `config.wsgi`) با تعداد worker برابر حافظه تقسیم بر ۲۰۰ مگابایت (۱ تا ۴) استفاده می‌کند؛ `WEB_CONCURRENCY` در متغیرهای برنامه آن را بازنویسی می‌کند. وقتی `psycopg2` (نه نسخهٔ binary) لازم باشد `libpq5` خودکار نصب می‌شود؛ اگر ساخت wheelها شکست بخورد، نصب با `build-essential` دوباره امتحان می‌شود.

## Node.js و Next.js

| فیلد | پیش‌فرض | توضیح |
| --- | --- | --- |
| `nodeVersion` (یا `node.version`) | `20` | ایمیج `node:<version>-alpine` |
| `build` (یا `node.build`) | برای nextjs: `npm run build` | فرمان ساخت |
| `start` (یا `node.start`) | node: `npm start`؛ nextjs: `next start` روی پورت | فرمان شروع |

با وجود `package-lock.json` از `npm ci` و در غیر این صورت از `npm install` استفاده می‌شود (وابستگی‌های dev برای ساخت نصب می‌شوند). `PORT` برابر `port` قرار می‌گیرد.

## سایت‌های ایستا

```json
{ "app": "site", "platform": "static", "static": { "root": "dist", "spa": true } }
```

| فیلد | پیش‌فرض | توضیح |
| --- | --- | --- |
| `static.root` (یا `root`) | `.` | پوشهٔ فایل‌ها |
| `static.spa` | `false` | برای مسیرهای ناشناخته `index.html` را سرو کن (برای مسیریاب‌های سمت کلاینت) |

با nginx و gzip سرو می‌شود. پوشه را پیش از `dockra deploy` بسازید، یا از `nextjs`/`docker` برای ساخت روی سرور استفاده کنید.

## Docker

با `"platform": "docker"`، `Dockerfile` خود پروژه ساخته می‌شود و `port` باید پورت داخل کانتینر باشد. برای PHP، Go، Rust یا هر چیز دیگری از این استفاده کنید. در شبکه‌های ایزوله خط‌های `# syntax=` را حذف کنید.

## نحو کران

```text
┌ minute (0-59)
│ ┌ hour (0-23)
│ │ ┌ day of month (1-31)
│ │ │ ┌ month (1-12)
│ │ │ │ ┌ day of week (0-7, 0 and 7 are Sunday)
* * * * * command
```

هر بخش `*`، یک عدد، یک بازه (`1-5`)، یک گام (`*/15`) یا یک فهرست (`1,15,30`) می‌پذیرد؛ مقادیر بیرون از بازهٔ هر بخش رد می‌شوند. کارها در منطقهٔ زمانی پلتفرم (`DOCKRA_TIME_ZONE`، پیش‌فرض UTC) داخل اولین کانتینر برنامه با `sh -c` و حداکثر `DOCKRA_CRON_TIMEOUT` ثانیه اجرا می‌شوند. اگر اجرای قبلی هنوز ادامه دارد، این اجرا رد می‌شود. آخرین خروجی و وضعیت در پنل نمایش داده می‌شود.
