# MagentoAudit

Modular Magento 2 health audit script focused on:

- extension/module validation
- database performance and index coverage
- indexer + mview/index state checks
- cache configuration review
- session backend detection (`files`, `db`, or `redis`)

## Files

- `magento_health_audit.py` - CLI entrypoint
- `magento_audit/base.py` - shared Magento/DB command helpers
- `magento_audit/modules/extensions.py` - extension/module checks
- `magento_audit/modules/database.py` - DB performance/index checks
- `magento_audit/modules/indexers.py` - indexer/index state checks
- `magento_audit/modules/cache.py` - cache status/config checks
- `magento_audit/modules/sessions.py` - session backend checks

## Usage

Run all modules:

```bash
python3 magento_health_audit.py --magento-root /path/to/magento
```

Run specific modules only:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --modules extensions,database,indexers,cache,sessions
```

Change output location:

```bash
python3 magento_health_audit.py \
  --magento-root /path/to/magento \
  --output-path /tmp/reports
```

## What is validated

### Extensions module
- enabled vs disabled modules
- third-party module footprint
- critical core modules accidentally disabled

### Database module
- DB size and largest tables
- MySQL runtime signals (slow query setting, InnoDB buffer hit ratio)
- tables without primary keys
- key index presence on important Magento tables

### Indexers module
- `bin/magento indexer:status`
- `bin/magento indexer:show-mode`
- `mview_state` and `indexer_state` review
- changelog backlog estimate (`*_cl` tables)

### Cache module
- cache type enable/disable state
- default and page cache backend detection (filesystem/db/redis)
- full page cache application mode and TTL

### Sessions module
- session save handler detection from `app/etc/env.php`
- reports backend details for Redis
- warns when sessions are files/database based
