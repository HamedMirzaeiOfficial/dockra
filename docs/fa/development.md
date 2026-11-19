# توسعه

[English](../development.md) · **فارسی**

## پیش‌نیازها

- Python 3.12، Node.js 20 به بالا (۲۲ پیشنهاد می‌شود)، Git
- Docker فقط اگر می‌خواهید درایور واقعی یا تست end-to-end را اجرا کنید

## اجرای محلی بدون Docker

درایور پیش‌فرض `fake` است: استقرارها، دیتابیس‌ها و بکاپ‌ها بدون دست زدن به Docker فوراً «تمام» می‌شوند (`DOCKRA_EAGER=1`). برای کار روی داشبورد، API و CLI همین کافی است.

```bash
python3 -m venv .venv && . .venv/bin/activate      # ویندوز: .venv\Scripts\activate
pip install -r api/requirements.txt -r api/requirements-dev.txt
cd api
python manage.py migrate
python manage.py seed_demo          # admin / dockra، برنامه‌ها و دیتابیس‌های نمونه و ۷ روز متریک
python manage.py runserver 127.0.0.1:8000
```

`seed` فقط سازمان و کاربر admin را می‌سازد؛ `seed_demo` دادهٔ نمونهٔ واقع‌گرایانه را **از طریق API عمومی** می‌سازد و اگر `DOCKRA_DRIVER=fake` نباشد اجرا نمی‌شود.

داشبورد:

```bash
cd web
npm ci
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run dev      # http://127.0.0.1:3000
```

CLI از داخل مخزن (تغییرات کد فوراً اعمال می‌شوند):

```bash
sh cli/install.sh            # ویندوز: .\cli\install.ps1، یا dockra.cmd در ریشهٔ مخزن
dockra login --api http://127.0.0.1:8000
```

تنظیمات مفید:

| متغیر | اثر |
| --- | --- |
| `SQLITE_PATH` | استفاده از یک فایل SQLite جدا (پیش‌فرض `api/db.sqlite3`) |
| `DOCKRA_DATA_ROOT` | پوشهٔ داده برای blobها، دیسک‌ها، media و پوشه‌های ساخت (پیش‌فرض `api/var`، `api/media`، `api/work`) |
| `DOCKRA_DRIVER=docker` | درایور واقعی روی Docker محلی شما |
| `DOCKRA_EAGER=0` | کارها به‌جای اجرای درجا در صف می‌روند؛ بعد `python manage.py run_worker` را اجرا کنید |
| `DJANGO_DEBUG=0` | حالتی شبیه production (به `DJANGO_SECRET_KEY` واقعی نیاز دارد) |

سرویس ترمینال: `python manage.py run_terminal --port 8001`.

## اجرای محلی با Docker

```bash
cd deploy
cp .env.example .env.local
docker compose -p dockra-local --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml \
  up -d --build traefik db api worker builder terminal
```

بعد داشبورد را مثل بالا با `npm run dev` اجرا کنید و `NEXT_PUBLIC_API_URL` را روی `http://127.0.0.1:8000` بگذارید.

## تست‌ها و بررسی‌ها

```bash
cd api
python manage.py test                                   # ۱۸۲ تست، حدود ۲ دقیقه، بدون Docker
python manage.py makemigrations --check --dry-run       # هماهنگی مدل‌ها و migrationها
cd .. && ruff check api cli                             # lint (پیکربندی در pyproject.toml)

cd web
npx tsc --noEmit                                        # نوع‌ها
npm run i18n:check                                      # هر متن پنل ترجمهٔ فارسی دارد
npx next build                                          # build نهایی
```

تست دود رابط کاربری و تصاویر (به API در حال اجرا با دادهٔ `seed_demo`، پنل در حال اجرا و Playwright نیاز دارد):

```bash
npm i -D playwright@1.49 && npx playwright install chromium
PANEL_URL=http://127.0.0.1:3000 node e2e/screenshots.cjs ../docs/screenshots dark       # با هر خطای کنسول شکست می‌خورد
PANEL_LANG=fa PANEL_URL=http://127.0.0.1:3000 node e2e/screenshots.cjs ../docs/screenshots dark   # نسخهٔ فارسی (*-fa.png)
```

end-to-end روی Docker واقعی (استک را نصب می‌کند، مستقر می‌کند، بدون قطعی دوباره مستقر می‌کند، بازمی‌گرداند، دیتابیس و بکاپ می‌سازد، شبکهٔ ایزوله را شبیه‌سازی می‌کند و در آخر همه‌چیز را پاک می‌کند):

```bash
sudo deploy/e2e.sh
```

### تست‌های API چه چیزی را پوشش می‌دهند

| فایل | تمرکز |
| --- | --- |
| `engine/tests.py` | ساخت و ویرایش برنامه و دیتابیس، استقرار، ظرفیت، کران، محدودیت‌های بیننده |
| `engine/test_platform.py` | رمزنگاری و پوشاندن رازها، جداسازی سازمان‌ها، برداشتن هم‌زمان کارها، بازیابی worker مرده، حفظ نسخهٔ فعال بعد از ساخت ناموفق |
| `engine/test_operations.py` | لغو و بازگشت، توزیع CLI و صفحه‌های ورود، لاگ زنده، تنظیمات شبکهٔ ایزوله |
| `engine/test_hardening.py` | محدودیت تلاش، proxyهای مورد اعتماد، بکاپ دیسک و ایمنی بازگردانی، مونت‌ها، ریاضی متریک‌ها |
| `engine/test_features.py` | آپلود افزایشی، وب‌هوک، قالب‌های ساخت، آینه‌ها، بررسی دامنه، محافظ SSRF |
| `engine/test_validation.py` | اعتبارسنجی دیسک و ریدایرکت |
| `engine/test_i18n.py` | زبان پاسخ از `Accept-Language`، برچسب‌های فارسی، جهت صفحهٔ CLI، ترجمه داشتن همهٔ پیام‌های API |
| `engine/test_import.py`، `test_disk_import.py`، `test_backups.py`، `test_dbadmin.py`، `test_logs.py`، `test_sharing.py`، `test_mirrors.py`، `test_db_observe.py` | مجموعه‌های مخصوص هر قابلیت |

تست‌ها هیچ‌وقت به ماشینی که رویش اجرا می‌شوند وابسته نیستند: ظرفیت با `DOCKRA_HOST_*` و ذخیرهٔ فضای دیسک با `DOCKRA_RESERVED_DISK_GB=0` هر جا لازم باشد بازنویسی می‌شود.

## ترجمه (انگلیسی و فارسی)

پنل و API هر دو یک مدل ساده دارند: **متن انگلیسی کلید است** و یک دیکشنری فارسی آن را به ترجمه‌اش نگاشت می‌کند. اگر ترجمه نباشد، متن انگلیسی خوانا نشان داده می‌شود، هیچ‌وقت کلید خام.

**پنل** (`web/`):

```tsx
import { msg, t } from "@/lib/i18n";

<Button>{t("Deploy")}</Button>
<p>{t("{n} files transferred.", { n: fmt(count) })}</p>
const TABS = [["logs", msg("Logs")]];   // msg() یک ثابت را علامت می‌زند؛ هنگام رندر t(label)
```

متن فارسی را در فایل مناسب در `web/lib/locales/fa/` (`common`، `apps`، `databases`، `platform`) اضافه کنید و `npm run i18n:check` را اجرا کنید؛ `--report` متن‌های JSX را که هنوز داخل `t()` نیستند هم فهرست می‌کند. قواعد:

- هیچ‌وقت جمله را از تکه‌ها نسازید (`"Deleted " + name`)؛ یک کلید با placeholder بسازید، چون ترتیب کلمات در زبان‌ها فرق دارد.
- اعداد و تاریخ‌ها از `fmt()`، `relTime()` و `dateTime()` در `lib/format.ts` بگذرند؛ این توابع به ارقام فارسی و تقویم شمسی تغییر می‌کنند.
- به‌جای `ml-`، `pr-`، `left-` و `text-right` از کلاس‌های منطقی Tailwind (`ms-`، `pe-`، `start-`، `text-end`) استفاده کنید و مقادیر فنی (نام میزبان، مسیر، فرمان، لاگ) را با `dir="ltr"` علامت بزنید.

**API** (`api/`):

```python
from config.i18n import tr

return Response({"detail": tr("Disk {name} is full", name=disk.name)}, status=400)
```

ترجمه‌های فارسی در `api/config/locale/fa.json` هستند؛ `python manage.py i18n_check` (که بخشی از تست‌ها هم هست) اگر ترجمه‌ای جا افتاده باشد شکست می‌خورد. برچسب choices مدل‌ها جایی ترجمه می‌شوند که نمایش داده می‌شوند (`tr(obj.get_status_display())`). کاری که worker انجام می‌دهد درخواستی ندارد و انگلیسی می‌ماند.

## Migrationها

برای اولین انتشار عمومی، migrationهای هر اپ در یک `0001_initial` ادغام شدند. از این به بعد:

- migrationها باید افزایشی و سازگار با نسخهٔ قبل باشند (کد قدیمی هنگام به‌روزرسانی بدون قطعی پلتفرم همچنان اجرا می‌شود)؛
- migrationهای داده باید عملیات معکوس یا یک `noop` صریح داشته باشند؛
- اگر `makemigrations --check` تغییر معوقی پیدا کند CI شکست می‌خورد.

## قراردادها

- **Python:** ruff (پیکربندی در `pyproject.toml`)، type hint برای کد جدید، قواعد کسب‌وکار در `engine/` و `*/validation.py`، viewها لاغر می‌مانند.
- **TypeScript:** حالت strict، پیش‌فرض‌های Prettier، رنگ‌ها فقط از توکن‌های طراحی ([design-system.md](design-system.md))، متن‌ها فقط از `t()`.
- **کامیت‌ها:** کوچک و متمرکز، با Conventional Commits (`feat:`، `fix:`، `docs:` و…). روی یک شاخه کار کنید و pull request باز کنید.
- **رازها:** هیچ‌وقت فایل `.env`، کلید، dump یا دیتابیس را کامیت نکنید؛ `.gitignore` موارد رایج را پوشش می‌دهد.

## انتشار CLI

1. `__version__` را در `cli/dockra.py` بالا ببرید. سرورها نسخهٔ مخزن را سرو می‌کنند و CLIهای نصب‌شده `dockra upgrade` را پیشنهاد می‌دهند.
2. برای فایل‌های اجرایی مستقل، تگ `cli-v<version>` را push کنید؛ `.github/workflows/cli-release.yml` آن‌ها را با PyInstaller برای لینوکس، ویندوز و macOS می‌سازد. آن‌ها را روی سرورها در `/var/lib/dockra/cli/` بگذارید.
