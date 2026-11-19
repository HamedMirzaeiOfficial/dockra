# Chapter 8 — Monitoring and alerts

**English** · [فارسی](../08-observability.md)

## Goal of this chapter

See where numbers like "290 requests per minute" and "p95 of 228 ms" come from, why computing them takes care, and what separates a good alert from a noisy one.

## Two data sources

| Source | What it gives | Where |
| --- | --- | --- |
| `docker stats` | CPU, memory, network per container | `engine/metrics.py` and `DockerDriver.usage_all` |
| Traefik's own Prometheus metrics | request counts, 5xx errors, response-time histogram | `engine/traffic.py` |

The worker runs `docker stats` for all containers **once** a minute (each run takes about two seconds) and writes the result into `MetricSample`. The panel never runs `docker stats` inside a user request; it reads the latest sample. That means a dashboard with 50 concurrent users does not load the server. The general rule: **move expensive work out of the request path.**

## Cumulative counters and deltas

Traefik says `traefik_service_requests_total{service="shop@docker"} 158230`. That number has been accumulating since Traefik started. For "requests per minute" you need the difference between two readings. `_delta` in `traffic.py`:

```python
before = _previous.get(key)
_previous[key] = value
if before is None:
    return 0.0
return value - before if value >= before else value
```

The last line matters: if Traefik restarts, the counter starts from zero and `value < before`. A negative difference makes no sense, so we assume the whole current value arrived after the restart. Prometheus's `rate()` has the same logic (counter reset).

## Quantiles from a histogram

An average response time is misleading: if 95 % of requests take 10 ms and 5 % take 5 seconds, the average is 260 ms, which no user ever experienced. p95 says "95 % of users got an answer faster than this".

Traefik does not keep the timings; it only says how many requests were under 0.1 s, how many under 0.3 s and so on (cumulative buckets). `_quantile` finds the bucket where the 95 % rank falls and **interpolates linearly inside that bucket**, exactly like Prometheus's `histogram_quantile`. So p95 is an estimate, and its accuracy depends on the bucket boundaries. A test in `test_hardening.py` (around line 386) checks the computation with 100 fake requests.

## CPU: an instant or an interval?

`docker stats --no-stream` is a snapshot of roughly two seconds. A minutely cron job that pushes CPU to 100 % for 2 seconds shows up as 100 % on the chart if the sampling moment happens to coincide with it. The fix in `drivers.py`: the container's cumulative CPU counter (in nanoseconds) is read from the Docker API and the **average since the previous sample** is computed. The same counter-and-delta idea, this time for CPU.

## Alerts as a state machine

`engine/alerts.py::_set` keeps alerts in two states, `firing` and `resolved`:

- condition true and no active alert → a new row and **one** 🚨 notification.
- condition true and an alert is already active → only the message is updated, **no** new notification.
- condition gone and an alert was active → `resolved` and one ✅ notification.

Without this, a "CPU is high" message would go to Telegram every minute, and after a day the team would mute the channel. **An alert that gets ignored is worse than no alert.**

A few more decisions that reduce noise:

- CPU and memory must stay above the threshold for `DOCKRA_ALERT_MINUTES` **consecutive** minutes, not one moment.
- The 5xx error rate does not alert with fewer than 20 requests in the window (one error out of 2 requests is 50 %, but it means nothing).
- "Unreachable" only after three failures in a row.
- A stopped app keeps no active alert.

## Who watches the watcher?

If the worker dies, who sends the "worker is dead" alert? The worker and the terminal service each write a heartbeat (`ServiceBeat`) and watch **each other**. `/api/health` also returns 503 when a service is silent, so an external tool (Uptime Kuma and the like) can monitor the platform itself. A clean shutdown (SIGTERM) deletes the row, so scaling builders down raises no false alarm.

## Exercises

1. Run `_quantile` on paper with these buckets: `[(0.05, 10), (0.1, 70), (0.3, 100), (inf, 100)]` and `q=0.95`. Compare your answer with the test.
2. If Traefik restarts **twice** between two readings and the counter ends up larger than the previous number, what mistake does `_delta` make? Can it be detected?
3. Design a new alert rule: "the app's memory has grown steadily over the last hour" (a memory leak). Is the data in `MetricSample`? How do you keep the noise down?
4. Why do charts have at most 240 points? What problem would the browser have with 7 days of per-minute data (10,080 samples)?
