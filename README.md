# MagentoAudit

Comprehensive modular Magento 2 health and performance audit.

## Features

### Phase 1 (Immediate impact)
- Frontend metrics:
  - TTFB sampling
  - key page timings (home/category/product/checkout when discoverable)
  - page size + resource counts
  - throughput estimate
- Extension review + extension impact profiler:
  - enabled/disabled module health
  - third-party extension footprint
  - observer/plugin/preference complexity scoring
  - CLI bootstrap timing baseline
- Backend profiling:
  - query timing benchmarks + EXPLAIN JSON analysis
  - MySQL slow query log parsing
  - catalog counts and EAV overhead indicators
  - flat catalog setting checks
- Log analysis:
  - PHP slow logs (top slow scripts)
  - access logs (errors, slow routes, route groups, memory/cpu hints)
  - Magento exception/system/debug recurring patterns

### Phase 2 (Deep optimization)
- Database deep dive:
  - table size, fragmentation, PK/index checks
  - foreign-key gap heuristic
  - orphaned product relation checks
  - EAV bloat indicators
  - session cleanup + quote/order retention metrics
  - EXPLAIN plan warnings
- Magento hotspots:
  - attribute count, configurable child fanout
  - category depth
  - URL rewrite table size
  - search synonyms/stopwords
  - catalog rule and customer segment overhead
- Cache effectiveness:
  - cache status and backend detection
  - Redis hit/miss ratio when Redis is configured
  - response cache header probe (`X-Magento-Cache-Debug`, etc.)

### Phase 3 (Production hardening)
- Production readiness:
  - deploy mode
  - compile/static state heuristic
  - cron and queue health
  - search backend endpoint probe
  - CDN/base static-media URL checks
- Security audit:
  - custom admin URL check
  - Two-factor auth module status
  - `env.php` permission checks
  - basic webroot exposure checks
  - risky extension keyword indicators
- Capacity testing (guarded):
  - optional concurrent load tests (disabled by default)

### Recommendations engine
- Generates prioritized action items:
  - `critical`, `high`, `medium`, `low`
  - based on module findings

## Files

- `magento_health_audit.py` - CLI entrypoint + phased orchestration
- `bootstrap.py` - single-file downloader/runner for `curl | python3` workflows
- `magento_audit/base.py` - shared Magento/DB command helpers
- `magento_audit/recommendations.py` - recommendation engine
- `magento_audit/modules/*.py` - audit modules

## Usage

### Bootstrap usage (`curl | python3`)

You do **not** need to copy/clone the whole repo manually on the server.
Use `bootstrap.py` to download a tarball to a temp folder and run `magento_health_audit.py`.

Public repo example:

```bash
curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/REF/bootstrap.py" | \
python3 - -- --magento-root /home/master/applications/db_name/public_html --phase phase1
```

Private repo example (token from env):

```bash
export GITHUB_TOKEN="ghp_xxx"
curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/REF/bootstrap.py" | \
python3 - --repo owner/private-repo --ref main -- --magento-root /home/master/applications/db_name/public_html --phase phase1
```

Cloudways-style logs example:

```bash
curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/REF/bootstrap.py" | \
python3 - -- \
  --magento-root /home/master/applications/db_name/public_html \
  --log-path /home/master/applications/db_name/logs \
  --phase phase2
```

Recommended hardening:

- Pin `--ref` to a commit SHA in production runs.
- Keep the `--` separator so bootstrap args and audit args are clearly separated.

Run default phase (`phase1`):

```bash
python3 magento_health_audit.py --magento-root /path/to/magento
```

Run deeper phase:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --phase phase2
```

Run full hardening phase:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --phase phase3
```

Run explicit modules:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --modules frontend,backend,logs,database,indexers,cache,sessions
```

Provide site/log paths:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --site-url https://store.example.com \
  --log-path /var/log/nginx
```

Enable active capacity testing:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --phase phase3 \
  --run-capacity-tests \
  --capacity-levels 5,10,20,50 \
  --capacity-duration 8
```

Save output report:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --output-path /tmp/reports
```
