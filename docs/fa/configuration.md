# پیکربندی

[English](../configuration.md) · **فارسی**

همهٔ تنظیمات متغیرهای محیطی در `deploy/.env` هستند (الگو: [`deploy/.env.example`](../../deploy/.env.example)). هر مقدار به کانتینرهای API، worker، builder و ترمینال می‌رسد. بعد از تغییر، `deploy/install.sh` را دوباره اجرا کنید.

## آدرس‌ها

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `PUBLIC_IP` | `127.0.0.1` | IP سرور؛ برای دامنه‌های sslip.io و راهنمای DNS استفاده می‌شود |
| `API_PUBLIC_URL` | `http://127.0.0.1:8000` | آدرس API که مرورگر استفاده می‌کند؛ در build پنل قرار می‌گیرد |
| `PANEL_ORIGIN` | `http://127.0.0.1:3000` | مبدأ پنل؛ تنها مبدأ CORS که API می‌پذیرد |
| `API_DOMAIN`، `PANEL_DOMAIN` | محاسبه‌شده | دامنه‌های پشت Traefik؛ `/ws` روی دامنهٔ API به سرویس ترمینال می‌رود |
| `API_BIND`، `PANEL_BIND` | `0.0.0.0` | رابط شبکه برای پورت‌های خام 8000/8001 و 3000؛ وقتی HTTPS کار کرد `127.0.0.1` بگذارید |
| `DOCKRA_APPS_DOMAIN` | خالی | دامنهٔ پیش‌فرض برنامه‌ها `<slug>.<domain>` با رکورد DNS wildcard؛ خالی یعنی `<slug>.<ip>.sslip.io` |
| `DJANGO_ALLOWED_HOSTS` | `*` | میزبان‌های مجاز API (جدا با کاما) |
| `DOCKRA_ORG_NAME` | `Default organization` | نام سازمان هنگام نصب اولیه |
| `DOCKRA_TIME_ZONE` | `UTC` | منطقهٔ زمانی پلتفرم، برای زمان‌بندی کران هم استفاده می‌شود |

## رازها

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | ساخته می‌شود | نشست‌ها و بلیت‌ها را امضا می‌کند؛ در production با مقدار placeholder اجرا نمی‌شود |
| `DOCKRA_ENCRYPTION_KEY` | ساخته می‌شود | متغیرها، رمز دیتابیس‌ها، رمز وب‌هوک‌ها و کلیدهای TOTP را رمزنگاری می‌کند. **بعد از نصب هرگز تغییرش ندهید**؛ در نبودش `DJANGO_SECRET_KEY` استفاده می‌شود |
| `POSTGRES_PASSWORD` | ساخته می‌شود | رمز دیتابیس پلتفرم |
| `DOCKRA_ADMIN_PASSWORD` | ساخته می‌شود | رمز `admin` فقط در اولین اجرا |

## ظرفیت

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DOCKRA_HOST_CPU`، `DOCKRA_HOST_MEMORY_MB`، `DOCKRA_HOST_DISK_GB` | `0` (تشخیص خودکار) | ظرفیت سرور |
| `DOCKRA_RESERVED_CPU`، `_MEMORY_MB`، `_DISK_GB` | `1`، `2048`، `20` | سهم کنار گذاشته‌شده برای پلتفرم و سیستم‌عامل |
| `DOCKRA_CPU_BURST` | `3` | وقتی سرور بیکار است، برنامه می‌تواند تا این ضریب از هسته‌هایش استفاده کند (۱ = سقف سخت) |
| `DOCKRA_CPU_OVERCOMMIT`، `DOCKRA_MEMORY_OVERCOMMIT` | `2`، `1.2` | رزروها تا این ضریب از ظرفیت پذیرفته می‌شوند |
| `DOCKRA_MEMORY_REAL_PERCENT` | `85` | اگر مصرف واقعی حافظهٔ سرور بالاتر از این باشد، برنامهٔ جدید یا بزرگ‌تر رد می‌شود |
| `DOCKRA_CPU_PRESSURE_PERCENT` | `25` | اگر پروسه‌ها این سهم از دقیقهٔ گذشته را منتظر CPU بوده‌اند هشدار بده (PSI) |
| `DOCKRA_BUILDERS` | `1` | تعداد ساخت هم‌زمان (هر کدام حدود نیم هسته) |

## ساخت

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DOCKRA_PIP_INDEX_URL` | خالی | آینهٔ PyPI برای ساخت برنامه‌ها و ایمیج پلتفرم |
| `DOCKRA_PIP_EXTRA_INDEX_URL` | خالی | index ثانویه که وقتی آینه خراب است اول امتحان می‌شود |
| `DOCKRA_PIP_FALLBACK_MIRROR` | خالی | جایگزین بعدی؛ زنجیره همیشه با `https://pypi.org/simple` تمام می‌شود |
| `DOCKRA_NPM_REGISTRY` | خالی | آینهٔ npm |
| `DOCKRA_APT_MIRROR` | خالی | آینهٔ Debian به‌جای `http://deb.debian.org` |
| `DOCKRA_DOCKER_MIRRORS` | خالی | آینه‌های Docker Hub (جدا با فاصله)؛ در تنظیمات daemon نوشته و برای ایمیج‌های پایه امتحان می‌شوند |
| `DOCKRA_BUILD_TIMEOUT` | `1800` | ثانیه برای ساخت هر ایمیج |
| `DOCKRA_RELEASE_TIMEOUT` | `900` | ثانیه برای فرمان انتشار |
| `DOCKRA_HEALTH_TIMEOUT` | `90` | ثانیه تا نسخهٔ جدید سالم شود |
| `DOCKRA_BUILD_CACHE_HOURS` | `720` | مدت نگهداری کش BuildKit (شامل pip و npm) |
| `DOCKRA_BLOB_KEEP_DEPLOYS` | `5` | فایل‌های آپلودشدهٔ این تعداد استقرار آخر هر برنامه نگه داشته می‌شود |

## عملیات

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DOCKRA_CRON_TIMEOUT` | `600` | ثانیه برای هر اجرای کران |
| `DOCKRA_EXEC_TIMEOUT` | `120` | ثانیه برای فرمان‌های کنسول و `dockra exec` |
| `DOCKRA_JOB_STALE_SECONDS` | `120` | کار در حال اجرایی که این مدت ساکت بماند ناموفق علامت می‌خورد |
| `DOCKRA_LOG_DAYS`، `DOCKRA_LOG_KEEP` | `14`، `50` | مدت و تعداد نگهداری لاگ بایگانی هر برنامه |
| `DOCKRA_METRICS_DAYS` | `7` | مدت نگهداری تاریخچهٔ مصرف |
| `DOCKRA_TERMINAL_PUBLIC_URL` | خالی | آدرس عمومی WebSocket ترمینال اگر متفاوت است (مثلاً `wss://api.example.com/ws`) |
| `DOCKRA_TERMINAL_PORT` | `8001` | پورت سرویس ترمینال |
| `DOCKRA_PANEL_CONNECT_SRC` | خالی | مبدأهای اضافه که CSP پنل اجازه می‌دهد (جدا با فاصله) |
| `DOCKRA_DBADMIN_IDLE_MINUTES` | `60` | زمان بیکاری پیش از بسته شدن رابط مدیریت دیتابیس |
| `DOCKRA_PGADMIN_IMAGE`، `DOCKRA_PHPMYADMIN_IMAGE`، `DOCKRA_MONGO_EXPRESS_IMAGE`، `DOCKRA_REDIS_COMMANDER_IMAGE` | ایمیج‌های رسمی | جایگزین برای رجیستری‌های خصوصی |

## بکاپ

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DOCKRA_AUTO_BACKUP_HOURS` | `24` | فاصلهٔ پیش‌فرض بکاپ دیتابیس (۰ = خاموش)؛ برای هر دیتابیس قابل تغییر |
| `DOCKRA_BACKUP_KEEP` | `3` | تعداد بکاپ آمادهٔ نگه‌داشته‌شده برای هر دیتابیس (و برای بکاپ روزانهٔ خود پلتفرم) |
| `DOCKRA_BACKUP_RETRY_MINUTES` | `60` | فاصلهٔ تلاش دوباره برای بکاپ خودکار ناموفق |
| `DOCKRA_DISK_BACKUP_HOURS`، `DOCKRA_DISK_BACKUP_KEEP` | `24`، `3` | همین تنظیمات برای دیسک برنامه‌ها |
| `DOCKRA_ORPHAN_BACKUP_DAYS` | `30` | مدت نگهداری بکاپ‌های دیسکی که حذف شده |
| `DOCKRA_S3_ENDPOINT`، `_BUCKET`، `_ACCESS_KEY`، `_SECRET_KEY` | خالی | نسخهٔ خارج از سرور در هر فضای ذخیره‌سازی سازگار با S3 |
| `DOCKRA_S3_REGION` | `us-east-1` | region برای SigV4 |
| `DOCKRA_S3_PREFIX` | `dockra` | پیشوند کلیدها: `<prefix>/databases/<name>/…`، `<prefix>/panel/…` |
| `DOCKRA_PANEL_PG_IMAGE` | `postgres:16` | ایمیج `pg_dump` برای بکاپ پلتفرم؛ باید با نسخهٔ سرویس `db` یکی باشد |

## TLS

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `ACME_EMAIL` | | ایمیل حساب Let's Encrypt |
| `DOCKRA_TLS_RESOLVER` | `le` | resolver گواهی Traefik؛ خالی یعنی فقط گواهی‌های `/var/lib/dockra/certs` |
| `DOCKRA_HSTS_SECONDS` | `0` | HSTS روی HTTPS پنل و API؛ فقط وقتی HTTPS پایدار شد فعال کنید |

## پایش و اعلان

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DOCKRA_ALERT_PERCENT` | `90` | آستانهٔ CPU، حافظه و دیسک |
| `DOCKRA_ALERT_MINUTES` | `5` | تعداد دقیقه‌های پیاپی بالاتر از آستانه |
| `DOCKRA_ERROR_RATE_PERCENT` | `5` | آستانهٔ نرخ 5xx (با حداقل ۲۰ درخواست در بازه) |
| `DOCKRA_TRAEFIK_METRICS_URL` | `http://traefik:8082/metrics` | endpoint پرومتئوس Traefik (فقط داخلی)؛ خالی یعنی بدون آمار درخواست |
| `DOCKRA_PROBE_URL` | `http://traefik` | پایهٔ کاوش‌های «در دسترس نیست»؛ خالی یعنی خاموش |
| `DOCKRA_TELEGRAM_BOT_TOKEN`، `DOCKRA_TELEGRAM_CHAT_ID` | خالی | اعلان تلگرام |
| `DOCKRA_TELEGRAM_API` | `https://api.telegram.org` | هر API یا proxy سازگار با تلگرام |
| `DOCKRA_NOTIFY_WEBHOOK` | خالی | JSON به شکل `POST {event, text, …}` دریافت می‌کند |

## امنیت

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DOCKRA_TRUSTED_PROXIES` | `traefik` | proxyهایی که `X-Forwarded-For` آن‌ها پذیرفته می‌شود (نام یا IP) |
| `DOCKRA_LOGIN_MAX_FAILS`، `DOCKRA_LOGIN_WINDOW_SECONDS` | `8`، `900` | محدودیت تلاش ورود |
| `DOCKRA_CLI_TOKEN_DAYS` | `365` | عمر توکن‌هایی که `dockra login` صادر می‌کند |
| `DOCKRA_DNS_SERVERS` | `1.1.1.1,8.8.8.8` | resolverهای بررسی رکورد دامنه وقتی resolver کانتینر کار نکند |

## فقط برای توسعه

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `DOCKRA_DRIVER` | `fake` | روی سرور `docker` (compose تنظیمش می‌کند) |
| `DOCKRA_EAGER` | با fake برابر `1` | اجرای کارها داخل همان درخواست |
| `SQLITE_PATH` | `api/db.sqlite3` | فایل SQLite وقتی تنظیمات `DATABASE_URL`/Postgres نیست |
| `DOCKRA_DATA_ROOT` | `api/var` | پوشهٔ داده برای اجرای محلی |

> زبان پنل تنظیم سمت سرور ندارد: هر کاربر در منوی کناری English یا فارسی را انتخاب می‌کند و پیش از آن زبان مرورگرش استفاده می‌شود.
