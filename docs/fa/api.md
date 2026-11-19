# مرجع REST API

[English](../api.md) · **فارسی**

آدرس پایه: `API_PUBLIC_URL` (مثلاً `https://api.example.com`). مسیرها با و بدون `/` انتهایی کار می‌کنند.

## قراردادها

- **احراز هویت:** `Authorization: Bearer <token>`؛ یا JWT ورود (`access`، ۱۲ ساعت) یا توکن API (`dockra_…`). JWTها با خروج، تغییر رمز، بازنشانی توسط مدیر یا غیرفعال شدن بلافاصله باطل می‌شوند؛ توکن‌های API با غیرفعال شدن یا بازنشانی توسط مدیر.
- **سازمان:** کاربرانی که چند عضویت دارند می‌توانند `X-Organization: <slug>` بفرستند؛ وگرنه اولین عضویت استفاده می‌شود.
- **نقش‌ها:** خواندن (GET) برای همهٔ اعضا؛ نوشتن به نقش توسعه‌دهنده نیاز دارد مگر جایی که *admin* علامت خورده ([نقش‌ها](security.md#نقش‌ها)).
- **زبان:** `Accept-Language: fa` پیام‌های خطا (`detail`) و برچسب‌ها (`status_label`، `kind_label` و…) را فارسی برمی‌گرداند؛ بدون آن انگلیسی. مقادیر ماشینی (`status`، `kind`، کلیدها) هیچ‌وقت ترجمه نمی‌شوند.
- **خطاها:** `{"detail": "…"}` با 400، 401، 403، 404، 409، 413، 429 یا 502.
- **کارهای صفی** (استقرار، روشن/خاموش، بکاپ و…): `202` یعنی در صف رفت، `200` یعنی همان‌جا انجام شد (درایور fake محلی). پیشرفت را روی خود منبع دنبال کنید (مثلاً `deployments`). شکست فوری `400` با `detail` برمی‌گرداند.
- **زمان‌ها:** ISO 8601.

## احراز هویت و حساب

| متد و مسیر | توضیح |
| --- | --- |
| `POST /api/auth/login` | `{username, password, otp?}` ← `{access, refresh, user}`. با 2FA و بدون کد: `400 {otp_required: true}`. محدودیت تلاش: `429 {retry_after}`. بدون احراز هویت |
| `GET /api/auth/me` | `id, username, name, initials, role, role_label, organization{name, slug}` |
| `POST /api/auth/logout` | ابطال همهٔ نشست‌های پنل این کاربر (204)؛ توکن‌های API می‌مانند |
| `POST /api/auth/password` | `{old_password, new_password}` ← `{access}` (JWT تازه؛ نشست‌های دیگر باطل می‌شوند) |
| `GET /api/auth/2fa` | `{enabled}` |
| `POST /api/auth/2fa/setup` | `{secret, otpauth_url}` (هنوز فعال نیست) |
| `POST /api/auth/2fa/confirm` | `{code}` ← `{enabled: true, recovery_codes: [8]}` |
| `POST /api/auth/2fa/disable` | `{password, code}` ← `{enabled: false}` |
| `GET /api/auth/tokens` | توکن‌های خود کاربر (هر نقشی): `id, name, prefix, created_at, last_used_at, expires_at` |
| `POST /api/auth/tokens` | `{name, expires_days?}` (پیش‌فرض ۹۰؛ `null`/`0` یعنی بدون انقضا) ← `{id, name, prefix, token, expires_at}`؛ `token` فقط یک بار نمایش داده می‌شود |
| `DELETE /api/auth/tokens/<id>` | ابطال |
| `GET /api/members` | `id, username, name, role, role_label, is_active, is_self` |
| `POST /api/members` | *admin*. `{username, password, role: admin\|developer\|viewer, first_name?, last_name?}`؛ فقط مالک نقش `admin` می‌دهد |
| `PATCH /api/members/<id>` | *admin*. `{role?, is_active?}`؛ نه برای خودتان و نه برای مالک؛ فقط مالک مدیران را تغییر می‌دهد |
| `DELETE /api/members/<id>` | *admin*. همان قواعد |
| `POST /api/members/<id>/password` | *admin*. `{password}`؛ نشست‌ها و توکن‌های آن کاربر باطل می‌شوند (204) |
| `GET /api/audit` | `id, action, target, detail, actor, created_at` |

## نمای کلی

| متد و مسیر | توضیح |
| --- | --- |
| `GET /api/health` | `{ok, driver, services: [{name, kind, queues, last_seen, ok}]}`؛ بدون احراز هویت؛ وقتی worker، builder یا terminal ۹۰ ثانیه ساکت باشد **503** |
| `GET /api/dashboard` | مجموع‌ها، ظرفیت (`cpu`، `memory_mb`، `disk_gb` × کل، رزرو، قابل‌تخصیص، مصرف‌شده، آزاد)، سرویس‌ها، استقرارهای اخیر، برنامه‌ها |
| `GET /api/monitoring` | `{apps (with metrics), databases}` |
| `GET /api/alerts?status=firing\|resolved\|all` | `id, app, app_name, kind, kind_label, message, status, started_at, resolved_at` |
| `GET /api/domains` | همهٔ دامنه‌های سازمان |

## برنامه‌ها

| متد و مسیر | توضیح |
| --- | --- |
| `GET /api/apps` | فهرست |
| `POST /api/apps` | `{name, slug, platform?, port?, instances?, cpu?, memory_mb?, git_url?, git_branch?}` ← برنامه (201) |
| `GET /api/apps/<slug>` | برنامه به‌همراه `env, domains, redirects, disks, cron, metrics, pipeline` |
| `PATCH /api/apps/<slug>` | هر کدام از `name, instances, cpu, memory_mb, port, platform, git_url, git_branch, health_path` |
| `DELETE /api/apps/<slug>` | *admin*. کانتینرها، دیسک‌ها و لاگ‌ها را حذف می‌کند؛ 204 یا `202 {job}` |
| `POST /api/apps/<slug>/restart` \| `stop` \| `start` | ← `{app, job}` |
| `POST /api/apps/<slug>/rollback` | `{deployment_id}` ← `{deployment, job, app}` |
| `GET /api/apps/<slug>/deployments` | `id, number, version, status, status_label, message, image, actor, log, is_current, created_at, finished_at` |
| `POST /api/apps/<slug>/deployments/<id>/cancel` | `202 {state: canceled\|canceling}`؛ اگر در صف یا در حال ساخت نباشد 400 |
| `GET /api/apps/<slug>/metrics?range=1h\|6h\|24h\|7d` | آخرین دقیقه (`cpu, ram, network_mbps, requests_per_min, errors_5xx_per_min, error_rate, avg_ms, p95_ms` و…) و `series[…]` |

فیلدهای برنامه: `id, name, slug, platform, platform_label, status (running|deploying|stopped|failed), status_label, instances, desired_instances, port, cpu, memory_mb, git_url, git_branch, health_path, webhook_enabled, runtime, current_image, region, region_label, created_at`.

### استقرار

| متد و مسیر | توضیح |
| --- | --- |
| `POST /api/apps/<slug>/sync` | `{files: [{path, hash (sha256), size, mode}]}` ← `{missing: [hash], bytes, files}`. مسیرهای نسبی بدون `..`؛ حداکثر ۵۰٬۰۰۰ فایل |
| `POST /api/apps/<slug>/blobs` | tar/tar.gz خام که نام اعضایش هش محتواست؛ حداکثر ۲ گیگابایت ← `{stored}`؛ ناهمخوانی هش 400 |
| `POST /api/apps/<slug>/deploy` | JSON `{message, files}` بعد از sync/blobs (اگر blobی نباشد `409 {missing}`)؛ multipart `{message, file: tar.gz}`؛ یا JSON `{message, git_url?}` ← `{deployment, job}` |

### لاگ و اجرا

| متد و مسیر | توضیح |
| --- | --- |
| `GET /api/apps/<slug>/logs?deployment=<id>` | `{deployment, text, updated_at}`؛ لاگ ساخت به‌همراه ۴۰۰ خط آخر کانتینر |
| `POST /api/apps/<slug>/logs/ticket?tail=200` | `{url, ticket}` برای لاگ زنده روی WebSocket (frameهای باینری از بایت‌های خام لاگ؛ frameهای متنی `{"type":"exit"}` / `{"type":"error","detail"}`)؛ حداکثر یک ساعت |
| `GET /api/apps/<slug>/logs/archive` | `[{name, release, ended_at, size}]` |
| `GET /api/apps/<slug>/logs/archive/<name>` | `text/plain` |
| `GET /api/apps/<slug>/logs/search?q=&limit=500` | `{matches: [{source, line}], truncated}`؛ `q` حداقل دو نویسه |
| `POST /api/apps/<slug>/exec` | `{command, cwd?}` ← `{output, exit_code, cwd}` |
| `POST /api/apps/<slug>/terminal` | `{url, ticket}`؛ اگر سرویس ترمینال پایین باشد 503 |

**پروتکل WebSocket ترمینال.** کلاینت ← سرور: frameهای باینری (بایت‌های ورودی) یا JSON به شکل `{"type":"input","data":"…"}` و `{"type":"resize","cols":N,"rows":N}`. سرور ← کلاینت: frameهای باینری (خروجی ترمینال)، و در پایان `{"type":"exit","code":N}` یا `{"type":"error","detail":"…"}`.

### متغیرها، دامنه‌ها، ریدایرکت‌ها، دیسک‌ها، کران

| متد و مسیر | توضیح |
| --- | --- |
| `GET /api/apps/<slug>/env` | `[{id, key, value, is_secret}]`؛ مقدار متغیرهای محرمانه برای بیننده‌ها `••••••` است |
| `PUT /api/apps/<slug>/env` | `{items: [{key, value, is_secret}]}`؛ **کل فهرست را جایگزین می‌کند**؛ برگرداندن ماسک، راز ذخیره‌شده را نگه می‌دارد |
| `GET /api/apps/<slug>/domains` | `id, hostname, kind (sslip\|custom), ssl_status (pending\|active\|error), is_primary, redirect_https, created_at` |
| `POST /api/apps/<slug>/domains` | `{hostname}` |
| `GET /api/apps/<slug>/domains/<id>/check` | `{state: ok\|missing\|mismatch\|proxied, message, found, expected}` |
| `PATCH /api/apps/<slug>/domains/<id>` | `{redirect_https}` |
| `DELETE /api/apps/<slug>/domains/<id>` | دامنهٔ اصلی: 400 |
| `GET\|POST /api/apps/<slug>/redirects` | `source_host (empty = all), source_path, match (prefix\|exact), target, keep_path, permanent`؛ حلقه و تکرار 400 |
| `PATCH\|DELETE /api/apps/<slug>/redirects/<id>` | |
| `GET\|POST /api/apps/<slug>/disks` | `{name, mount_to, size_gb}` |
| `PATCH /api/apps/<slug>/disks/<name>` | `{size_gb? (grow only), mount_to?, backup_hours?, backup_keep?}` |
| `DELETE /api/apps/<slug>/disks/<name>` | `{job}`؛ کانتینرها بدون دیسک دوباره راه‌اندازی می‌شوند و بعد داده پاک می‌شود |
| `GET /api/apps/<slug>/disks/<name>/files?path=/` | `{path, entries: [{name, type, size, modified}]}` |
| `DELETE /api/apps/<slug>/disks/<name>/files?path=` | حذف یک فایل یا پوشه |
| `GET /api/apps/<slug>/disks/<name>/download?path=` | فایل، یا پوشه به‌صورت tar.gz؛ بیننده‌ها 403 |
| `POST /api/apps/<slug>/disks/<name>/upload?path=` | multipart `file`، یا tar/tar.gz خام که در مسیر باز می‌شود |
| `POST /api/apps/<slug>/disks/<name>/import` | tar/zip خام (`?replace=1`) یا JSON `{url}` / `{ftp: {…}}` ← `{job}` |
| `GET\|POST /api/apps/<slug>/disks/<name>/backups` | فهرست / ساخت بکاپ دیسک |
| `POST /api/apps/<slug>/disks/<name>/backups/<id>/restore` | بازگردانی (اول یک بکاپ احتیاطی گرفته می‌شود) |
| `GET\|POST /api/apps/<slug>/cron` | `{schedule, command}` ← `id, schedule, command, enabled, last_run_at, last_status, last_output` |
| `PATCH\|DELETE /api/apps/<slug>/cron/<id>` | |

### وب‌هوک‌ها

| متد و مسیر | توضیح |
| --- | --- |
| `GET /api/apps/<slug>/webhook` | `{enabled, url}` |
| `POST /api/apps/<slug>/webhook` | ساخت یا تعویض ← `{enabled, url, secret}`؛ `git_url` لازم است |
| `DELETE /api/apps/<slug>/webhook` | غیرفعال‌سازی |
| `POST /api/hooks/<slug>` | دریافت pushها؛ بدون احراز هویت، با `X-Hub-Signature-256`، `X-Gitea-Signature`، `X-Gitlab-Token` یا `?token=` بررسی می‌شود. `ping` ← `{pong}`؛ شاخهٔ دیگر ← `{skipped}`؛ موفق `202 {deployment, version}` |

## دیتابیس‌ها

| متد و مسیر | توضیح |
| --- | --- |
| `GET /api/databases` | فهرست |
| `POST /api/databases` | `{name, engine, version?, cpu?, memory_mb?, disk_gb?}` |
| `POST /api/databases/import` | بدنهٔ dump خام با گزینه‌ها در query string، یا `?upload=<id>` / `?url=` ← `{job, database}` |
| `GET /api/databases/<id>` | جزئیات؛ `password` و `url` برای غیربیننده‌ها |
| `PATCH /api/databases/<id>` | `{cpu?, memory_mb?, disk_gb? (grow only), backup_hours?, backup_keep?}` |
| `POST /api/databases/<id>/stop` \| `start` | ← `{database, job}` |
| `DELETE /api/databases/<id>` | *admin* |
| `POST /api/databases/<id>/link` | `{app, env_key?}` |
| `POST /api/databases/<id>/backup` | ← `{backup, job}` |
| `DELETE /api/databases/<id>/backups/<bid>` | |
| `GET /api/databases/<id>/backups/<bid>/download` | فایل؛ بیننده‌ها 403 |
| `POST /api/databases/<id>/restore` | *admin*. JSON `{backup_id}` یا یک فایل ← `{job}` |
| `POST /api/databases/<id>/tunnel` | `{url, ticket}`؛ WebSocket که TCP خام را رد و بدل می‌کند |
| `GET\|POST\|DELETE /api/databases/<id>/admin` | وضعیت / باز کردن / بستن رابط مدیریت وب |

## آپلود و کارها

| متد و مسیر | توضیح |
| --- | --- |
| `POST /api/uploads` | شروع آپلود قابل ادامه ← `{upload}` |
| `GET /api/uploads/<id>` | `{size}` دریافت‌شده تا این لحظه |
| `PUT /api/uploads/<id>?offset=N` | افزودن یک تکه؛ offset اشتباه ← `409 {size}` |
| `GET /api/jobs/<id>` | `{status, log, result}` کارهای طولانی (درون‌ریزی، بازگردانی) |

## توزیع CLI و ورود مرورگری

| مسیر | توضیح |
| --- | --- |
| `POST /api/auth/cli/start` | ← `{code}` |
| `GET /cli?code=` | صفحهٔ ورود و تأیید در مرورگر (به زبان مرورگر: انگلیسی یا فارسی) |
| `GET /api/auth/cli/poll?code=` | `{status: pending}` یا یک بار `{status: ready, token, username}`؛ منقضی 410 |
| `POST /api/auth/cli/watch` | `{app, after}` ← `{code}` برای پیگیری استقرار در مرورگر `/cli/deploy?ticket=` |
| `GET /cli/install.sh` \| `/cli/install.ps1` | اسکریپت‌های نصب که به همین سرور اشاره می‌کنند |
| `GET /cli/dockra.py` | خود CLI، با `X-Dockra-Version` و `X-Dockra-Sha256` |
| `GET /cli/version` | `{version, binaries}` |
| `GET /cli/bin/<name>` | `dockra-linux`، `dockra-macos`، `dockra-windows.exe` |
