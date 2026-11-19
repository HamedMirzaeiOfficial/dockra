# نصب و نگهداری

[English](../installation.md) · **فارسی**

## پیش‌نیازها

| مورد | حداقل |
| --- | --- |
| سیستم‌عامل | لینوکس با systemd (Ubuntu 22.04 به بالا، Debian 12) |
| Docker | Docker Engine 24 به بالا با Compose 2.24 به بالا |
| سخت‌افزار | ۲ هسته، ۴ گیگابایت رم، ۴۰ گیگابایت دیسک. پلتفرم ۱ هسته، ۲ گیگابایت و ۲۰ گیگابایت را برای خودش رزرو می‌کند؛ بقیه برای برنامه‌ها و دیتابیس‌هاست |
| دسترسی | root یا sudo؛ پشتیبانی از loop device (دیسک‌ها ایمیج‌های ext4 روی loop هستند) |
| پورت‌ها | 80 و 443 (Traefik)؛ 8000 (API)، 8001 (ترمینال) و 3000 (پنل) تا وقتی پشت HTTPS نرفته‌اند |

## نصب

```bash
git clone <repository> dockra
cd dockra/deploy
cp .env.example .env
```

حداقل این‌ها را تنظیم کنید ([همهٔ تنظیمات](configuration.md)):

```bash
PUBLIC_IP=203.0.113.10                     # IP عمومی سرور
ACME_EMAIL=ops@example.com                 # حساب Let's Encrypt
API_PUBLIC_URL=http://203.0.113.10:8000    # جایی که پنل و CLI به API می‌رسند
PANEL_ORIGIN=http://203.0.113.10:3000      # مبدأ پنل (CORS)
DOCKRA_APPS_DOMAIN=apps.example.com        # اختیاری؛ وگرنه <app>.<ip>.sslip.io
```

بعد `./install.sh` را اجرا کنید. این اسکریپت:

1. `DJANGO_SECRET_KEY`، رمز دیتابیس پلتفرم، `DOCKRA_ENCRYPTION_KEY` و رمز admin را می‌سازد (فقط جایی که هنوز مقدار placeholder است)؛
2. CPU، حافظه و دیسک سرور را تشخیص می‌دهد و در `.env` می‌نویسد؛
3. اگر `API_DOMAIN` و `PANEL_DOMAIN` خالی باشند، آن‌ها را با `api.` و `panel.` روی دامنهٔ برنامه‌ها (یا sslip.io) پر می‌کند؛
4. آینه‌های Docker Hub را از `DOCKRA_DOCKER_MIRRORS` در `/etc/docker/daemon.json` می‌نویسد (Docker را فقط اگر تغییری بوده ری‌استارت می‌کند)؛
5. `/var/lib/dockra` را می‌سازد، ایمیج‌ها را build می‌کند، migrationها را یک بار اجرا و همهٔ سرویس‌ها را روشن می‌کند.

رمز admin چاپ و در `deploy/.admin-password` ذخیره می‌شود.

## اولین ورود

1. `http://<ip>:3000` را باز کنید و با `admin` وارد شوید. زبان پنل از مرورگر گرفته می‌شود و از منوی کناری قابل تغییر است.
2. `deploy/.admin-password` را پاک کنید.
3. در تنظیمات ورود دومرحله‌ای را فعال کنید.
4. برای هر نفر یک حساب با نقش مناسب بسازید ([امنیت](security.md)).
5. فرمان نصب CLI را که در صفحهٔ مستندات پنل آمده با تیم به اشتراک بگذارید.

## دامنه و HTTPS برای پنل و API

1. رکوردهای A برای `API_DOMAIN` و `PANEL_DOMAIN` (یا یک wildcard برای دامنهٔ برنامه‌ها) را به سرور اشاره دهید.
2. منتظر صدور گواهی‌ها بمانید یا گواهی خودتان را بدهید (پایین‌تر).
3. در `.env`:
   ```bash
   API_PUBLIC_URL=https://api.apps.example.com
   PANEL_ORIGIN=https://panel.apps.example.com
   API_BIND=127.0.0.1
   PANEL_BIND=127.0.0.1
   ```
4. `./install.sh` را دوباره اجرا کنید (پنل دوباره ساخته می‌شود، چون `API_PUBLIC_URL` داخل build آن است).
5. توسعه‌دهنده‌ها یک بار `dockra login --api https://api.apps.example.com` را اجرا کنند.

اگر سرویس دیگری پورت‌های 80/443 را گرفته، `-f docker-compose.small.yml` را اضافه کنید: Traefik با سهم منابع کمتر به 8080/8443 می‌رود. در این حالت چالش‌های HTTP-01 به یک proxy جلویی یا گواهی‌های خودتان نیاز دارند.

## به‌روزرسانی

```bash
cd dockra && git pull && cd deploy && ./install.sh
```

`install.sh` idempotent است و `.env` را نگه می‌دارد. ایمیج‌ها دوباره ساخته می‌شوند و migrationها پیش از ری‌استارت سرویس‌ها یک بار اعمال می‌شوند؛ به کانتینرهای برنامه‌ها و دیتابیس‌ها دست زده نمی‌شود و برنامه‌ها همچنان سرویس می‌دهند. اگر سرور به مخزن دسترسی ندارد، با `git archive` یک tarball بسازید و `deploy/update.sh <bundle>` را اجرا کنید، یا از ویندوز `deploy/push.ps1 -Server <host>` را بزنید. پیش از به‌روزرسانی‌های بزرگ یک بکاپ دستی بگیرید.

## کارهای پس‌زمینه

| هر | کار |
| --- | --- |
| ۱۵ ثانیه | ضربان سرویس‌ها و بررسی متقابل |
| ۳۰ ثانیه | ناموفق کردن کارهایی که workerشان رفته؛ اتصال دوبارهٔ Traefik به شبکهٔ برنامه‌ها |
| ۱ دقیقه | نمونهٔ مصرف، ارزیابی هشدارها، بستن رابط‌های مدیریت دیتابیس بیکار |
| ۱۰ دقیقه | بکاپ‌های خودکار موعدرسیدهٔ دیتابیس و دیسک |
| ۱ ساعت | پاک‌سازی (blobهای بی‌استفاده، لاگ‌های قدیمی، بلیت‌های منقضی ورود، کارهای قدیمی‌تر از ۳۰ روز)؛ بکاپ روزانهٔ دیتابیس پلتفرم |

## محل داده‌ها

| مسیر | محتوا |
| --- | --- |
| `/var/lib/dockra/images/*.img` | دیسک برنامه‌ها و دیتابیس‌ها (ایمیج‌های ext4) |
| `/var/lib/dockra/mnt/<name>/data` | نقطهٔ مونت آن‌ها (در `/etc/fstab` هم ثبت شده) |
| `/var/lib/dockra/media/backups/` | بکاپ دیتابیس‌ها؛ `panel/` بکاپ‌های روزانهٔ خود پلتفرم است |
| `/var/lib/dockra/blobs/` | فایل‌های آپلودشدهٔ استقرارها (محتوامحور) |
| `/var/lib/dockra/work/mirror/` | آخرین درخت فایل هر برنامه |
| `/var/lib/dockra/logs/<app>/` | لاگ بایگانی‌شدهٔ نسخه‌های قبلی |
| `/var/lib/dockra/certs/` | گواهی‌های خودتان برای Traefik |
| `/var/lib/dockra/cli/` | فایل‌های اجرایی آمادهٔ CLI |
| volume `pgdata` | دیتابیس پلتفرم |
| volume `traefik-certs` | گواهی‌های Let's Encrypt |

## بکاپ خود پلتفرم

worker هر روز از دیتابیس پلتفرم در `media/backups/panel/` (و در صورت تنظیم، در S3) dump می‌گیرد. برای بازیابی یک سرور: Dockra را روی سرور جدید با **همان** `.env` نصب کنید (کلید رمزنگاری باید یکی باشد)، آخرین dump پنل را در سرویس `db` بازگردانید، اگر `/var/lib/dockra` را دارید کپی کنید و بعد برنامه‌ها و دیتابیس‌ها را از پنل روشن کنید؛ کانتینرها با تنظیمات و رمزهای ذخیره‌شده دوباره ساخته می‌شوند.

## شبکه‌های محدود یا کاملاً ایزوله

Dockra بدون اینترنت عمومی کار می‌کند:

- **ایمیج‌ها:** وقتی آنلاین هستید `deploy/offline.sh pull` و `offline.sh save dockra-images.tar.gz` را اجرا کنید؛ روی سرور مقصد `offline.sh load dockra-images.tar.gz`. `offline.sh check` ایمیج‌های جاافتاده را فهرست می‌کند. با `DOCKRA_EXTRA_IMAGES` فهرست را گسترش دهید.
- **رجیستری‌ها و بسته‌ها:** `DOCKRA_DOCKER_MIRRORS`، `DOCKRA_PIP_INDEX_URL` (به‌علاوهٔ `DOCKRA_PIP_FALLBACK_MIRROR`)، `DOCKRA_NPM_REGISTRY` و `DOCKRA_APT_MIRROR` را روی آینه‌های داخلی خود تنظیم کنید. کش BuildKit (`DOCKRA_BUILD_CACHE_HOURS`) هم بسته‌هایی را که قبلاً دانلود شده‌اند سرو می‌کند.
- **DNS:** `DOCKRA_APPS_DOMAIN` را روی یک دامنهٔ wildcard داخلی بگذارید (sslip.io یک resolver عمومی است) و `DOCKRA_DNS_SERVERS` را روی resolverهای خودتان.
- **TLS:** `DOCKRA_TLS_RESOLVER=` (خالی) را تنظیم کنید و گواهی‌ها (`*.pem`) و یک پیکربندی پویای Traefik (`*.yml`) را در `/var/lib/dockra/certs/` بگذارید.
- **Dockerfileها:** خط‌های `# syntax=` را حذف کنید؛ این خط‌ها BuildKit را وادار به دریافت ایمیج frontend می‌کنند.

`deploy/e2e.sh` یک شبکهٔ ایزولهٔ شبیه‌سازی‌شده دارد تا این مسیر سالم بماند.

## حذف کامل

**این کار همهٔ برنامه‌ها، دیتابیس‌ها و بکاپ‌های سرور را پاک می‌کند.**

```bash
cd dockra/deploy
docker compose down -v --remove-orphans
docker ps -aq --filter label=dockra.role=app | xargs -r docker rm -f
docker ps -aq --filter name=dockra-db- | xargs -r docker rm -f
docker network rm dockra
for m in /var/lib/dockra/mnt/*; do sudo umount "$m" 2>/dev/null; done
sudo sed -i '\#/var/lib/dockra/images/#d' /etc/fstab
sudo rm -rf /var/lib/dockra
```
