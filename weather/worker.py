import logging
import time

from . import charts, store, stream


def tick():
    with store.db().begin() as con:
        stream.stale_scan(con)
    while stream.deliver():
        pass
    charts.synchronize()


if __name__=="__main__":
    store.migrate()
    while True:
        try:
            tick()
        except Exception:
            logging.exception("Ошибка фоновой обработки")
        time.sleep(3)
