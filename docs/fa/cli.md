# CLI به نام `dockra`

[English](../cli.md) · **فارسی**

یک فایل پایتون (`cli/dockra.py`) که فقط از کتابخانهٔ استاندارد استفاده می‌کند. آن را از سرور خودتان نصب کنید؛ به PyPI، npm یا GitHub نیازی نیست:

```bash
curl -fsSL https://api.example.com/cli/install.sh | sh      # لینوکس و macOS
irm https://api.example.com/cli/install.ps1 | iex           # PowerShell ویندوز
dockra login --api https://api.example.com
```

`dockra` بدون آرگومان یک برگهٔ راهنما چاپ می‌کند؛ `dockra <command> --help` همهٔ گزینه‌ها را توضیح می‌دهد. در پوشه‌ای که `dockra.json` (یا `liara.json`) دارد، شناسهٔ برنامه از فیلد `app` آن خوانده می‌شود؛ وگرنه `--app` را بدهید.

> خروجی‌های CLI انگلیسی‌اند؛ صفحه‌هایی که CLI در مرورگر باز می‌کند (ورود و پیگیری استقرار) به زبان مرورگر شما، انگلیسی یا فارسی، نمایش داده می‌شوند.

## شروع

| فرمان | توضیح |
| --- | --- |
| `dockra login` | مرورگر را باز می‌کند؛ درخواستی را تأیید کنید که کدش با کد ترمینال یکی است. برای محیط بدون مرورگر `--username/--password` |
| `dockra init <app>` | نوشتن `dockra.json` |
| `dockra deploy` | در صورت نیاز برنامه را می‌سازد، فقط فایل‌های تغییرکرده را آپلود می‌کند و ساخت را دنبال می‌کند؛ `--full` کل پوشه را آپلود می‌کند |
| `dockra apps` | فهرست برنامه‌ها |
| `dockra open` | باز کردن آدرس برنامه |
| `dockra upgrade` | به‌روزرسانی CLI از سرور (با بررسی sha256) |

## نسخه‌ها

| فرمان | توضیح |
| --- | --- |
| `dockra deployments` | تاریخچهٔ نسخه‌ها |
| `dockra logs [-f]` | لاگ ساخت و کانتینر؛ `-f` به‌صورت زنده |
| `dockra rollback v3` | بازگشت به یک نسخه |
| `dockra cancel [v5]` | لغو استقرار در صف یا در حال ساخت (بدون نسخه، همهٔ آن‌ها) |
| `dockra restart` / `stop` / `start` | چرخهٔ عمر |

## پیکربندی

| فرمان | توضیح |
| --- | --- |
| `dockra env set KEY=VALUE …` / `--file .env` | تنظیم متغیرها (خروجی JSON کلید/مقدار هم پذیرفته می‌شود) |
| `dockra env rm KEY …` | حذف متغیرها |
| `dockra domain add app.example.com` | افزودن دامنهٔ اختصاصی و نمایش رکورد DNS |
| `dockra domain check app.example.com` | بررسی رکورد |
| `dockra domain https app.example.com on` | اجبار HTTPS |
| `dockra redirect add old.example.com https://example.com` | ریدایرکت یک دامنه (مسیر حفظ می‌شود)؛ `/old /new` برای مسیرها، `--exact`، `--temporary`، `--drop-path` |
| `dockra cron add "0 3 * * * python manage.py clearsessions"` | کار زمان‌بندی‌شده |
| `dockra webhook enable` | چاپ آدرس و رمز وب‌هوک برای GitHub/GitLab/Gitea |

## دسترسی به محیط اجرا

| فرمان | توضیح |
| --- | --- |
| `dockra shell` | ترمینال تعاملی داخل کانتینر (`--line` برای حالت خطی در ویندوز) |
| `dockra exec -- python manage.py migrate` | اجرای یک فرمان |

## دیتابیس‌ها

| فرمان | توضیح |
| --- | --- |
| `dockra db create myapp-db --engine postgres` | همچنین `mysql`، `mariadb`، `mongodb`، `redis`؛ `--version` |
| `dockra db link myapp-db` | نوشتن `DATABASE_URL` (یا `REDIS_URL`) در برنامه؛ `--key` برای نام دیگر |
| `dockra db connect myapp-db` | تونل امن به `127.0.0.1:<port>` |
| `dockra db backups` / `download` / `restore` | بکاپ‌ها؛ بازگردانی اول یک بکاپ احتیاطی می‌گیرد |
| `dockra db import new-db --file dump.sql.gz --link` | ساخت دیتابیس از فایل dump (تشخیص موتور و نسخه) یا `--url` |
| `dockra db resize` / `stop` / `start` | منابع و چرخهٔ عمر |

## دیسک‌ها

| فرمان | توضیح |
| --- | --- |
| `dockra disk files media` | فهرست فایل‌ها |
| `dockra disk upload media ./uploads --to /` | آپلود یک فایل یا پوشه (`--contents` برای محتوای پوشه) |
| `dockra disk download media /uploads` | دانلود یک فایل یا یک پوشه به‌صورت tar.gz |
| `dockra disk import media --ftp ftp://user@host/` | دریافت فایل‌ها روی سرور از FTP (موازی، افزایشی)، از `--url` یا آرشیو `--file`؛ `--replace` اول دیسک را خالی می‌کند (بعد از بکاپ) |
| `dockra disk backup` / `backups` / `backup-download` / `restore` | بکاپ دیسک |
| `dockra disk resize media --size 10` | بزرگ کردن دیسک یا تغییر مسیر اتصال |
| `dockra disk rm media` | حذف دیسک (بکاپ‌هایش ۳۰ روز می‌مانند) |

## متغیرهای محیطی

| متغیر | توضیح |
| --- | --- |
| `DOCKRA_API` | آدرس API (ورود ذخیره‌شده را بازنویسی می‌کند) |
| `DOCKRA_TOKEN` | توکن API برای CI (در تنظیمات ← توکن‌های API بسازید) |

CLI اطلاعات ورود را در `~/.dockra.json` نگه می‌دارد که با مجوز 600 (فقط خواندن و نوشتن برای صاحب فایل) ساخته می‌شود.
