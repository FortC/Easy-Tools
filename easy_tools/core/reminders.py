"""Local reminders: no system toast, no catch-up storm, no automatic scripts."""
from datetime import datetime, timedelta
import threading


def next_due(item, now):
    mode = item.get('mode', 'once')
    if mode == 'once':
        value = datetime.fromisoformat(str(item.get('at', '')))
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        return value
    if mode == 'daily':
        hour, minute = map(int, item.get('at', '').split(':'))
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return due if due >= now else due + timedelta(days=1)
    if mode == 'interval':
        seconds = int(item.get('seconds', 0))
        if not 60 <= seconds <= 604800:
            raise ValueError('提醒间隔为 1 分钟～7 天')
        return now + timedelta(seconds=seconds)
    raise ValueError('未知提醒方式')


def validate_reminders(items):
    if not isinstance(items, list) or len(items) > 100:
        return ['提醒必须为列表，最多 100 条']
    errors, ids = [], set()
    for item in items:
        try:
            if not item.get('id') or item['id'] in ids:
                raise ValueError('提醒标识缺失或重复')
            ids.add(item['id'])
            if not str(item.get('title', '')).strip() or len(item['title']) > 500:
                raise ValueError('提醒内容为 1～500 字')
            next_due(item, datetime.now())
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
            errors.append('提醒配置无效，请检查内容、日期、时间或间隔')
    return errors


class ReminderScheduler:
    def __init__(self, store, deliver):
        self.store, self.deliver = store, deliver
        self._due = {}
        self._seen = set()
        self._stop = threading.Event()
        self._thread = None

    def tick(self, now=None):
        now = now or datetime.now()
        items = self.store.section('reminders', []) or []
        live = set()
        for item in items:
            if not item.get('enabled', True) or validate_reminders([item]):
                continue
            key = (item['id'], item.get('mode'), item.get('at'), item.get('seconds'))
            live.add(key)
            if item.get('mode') == 'once' and (key in self._seen or item.get('fired_at')):
                continue
            if key not in self._due:
                self._due[key] = next_due(item, now)
            due = self._due[key]
            if now < due:
                continue
            # Once reminders missed by >60s during shutdown/sleep are not replayed.
            if item.get('mode') != 'once' or (now - due).total_seconds() <= 60:
                self.deliver(item['title'])
            if item.get('mode') == 'once':
                self._seen.add(key)
                def consumed(cfg):
                    for row in cfg.get('reminders', []):
                        if row.get('id') == item['id'] and row.get('at') == item.get('at'):
                            row['fired_at'] = now.isoformat()
                self.store.mutate(consumed)
            else:
                self._due[key] = next_due(item, now + timedelta(seconds=1) if item.get('mode') == 'daily' else now)
        self._due = {k: v for k, v in self._due.items() if k in live}
        self._seen.intersection_update(live)

    def start(self):
        if self._thread:
            return
        def loop():
            while not self._stop.wait(1):
                try:
                    self.tick()
                except Exception:
                    # Malformed config / transient disk failure must not kill reminders.
                    from .log import get_logger
                    get_logger('reminders').warning('提醒调度失败，请检查配置和磁盘状态')
        self._thread = threading.Thread(target=loop, name='reminders', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
