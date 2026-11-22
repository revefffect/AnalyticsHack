"""Field photographs belong to an observed station in a specific dataset."""
from io import BytesIO
from urllib.parse import quote

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageOps

from meteo.access import pick_dataset


MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
FORMATS = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}


def station_photo_url(dataset_id, station_id):
    return f'/api/datasets/{quote(dataset_id, safe="")}/stations/{quote(station_id, safe="")}/photo'


def normalized_jpeg(payload, image_format):
    """Decode one frame, honor camera orientation and discard embedded metadata."""
    try:
        with Image.open(BytesIO(payload), formats=[image_format]) as source:
            if source.width * source.height > MAX_PIXELS:
                raise HTTPException(413, 'Фотография должна быть не более 16 Мп')
            if getattr(source, 'is_animated', False):
                raise HTTPException(422, 'Выберите фотографию из одного кадра')
            source.verify()
        with Image.open(BytesIO(payload), formats=[image_format]) as source:
            source.thumbnail((1600, 1600))
            oriented = ImageOps.exif_transpose(source).convert('RGBA')
            image = Image.new('RGB', oriented.size, 'white')
            image.paste(oriented, mask=oriented.getchannel('A'))
            output = BytesIO()
            image.save(output, 'JPEG', quality=88)
            return output.getvalue()
    except Image.DecompressionBombError as exc:
        raise HTTPException(413, 'Фотография должна быть не более 16 Мп') from exc
    except (OSError, ValueError, SyntaxError, EOFError) as exc:
        raise HTTPException(422, 'Повреждённое изображение или формат не соответствует Content-Type') from exc


def register_station_photos(app, current_store, actor):
    @app.get('/api/datasets/{dataset_id}/stations/{station_id}/photo')
    def get_photo(dataset_id: str, station_id: str, user=Depends(actor)):
        with current_store().connect() as db:
            dataset = pick_dataset(db, dataset_id, user)
            if dataset['source'] not in ('station', 'stream'):
                raise HTTPException(404, 'Фотография недоступна')
            photo = db.execute('SELECT image,media_type FROM station_photos WHERE dataset=? AND station=?',
                               (dataset_id, station_id)).fetchone()
        if photo is None:
            raise HTTPException(404, 'Фотография недоступна')
        return Response(photo['image'], media_type=photo['media_type'],
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    def check_editor(db, dataset_id, station_id, user):
        dataset = pick_dataset(db, dataset_id, user, permission='edit')
        if dataset['source'] not in ('station', 'stream') or not db.execute(
            'SELECT 1 FROM observations WHERE dataset=? AND station=? LIMIT 1', (dataset_id, station_id)
        ).fetchone():
            raise HTTPException(404, 'Станция недоступна в этом наборе')

    def save_photo(store, user, dataset_id, station_id, credit, image):
        with store.connect(write=True) as db:
            check_editor(db, dataset_id, station_id, user)
            db.execute("""
                INSERT INTO station_photos (dataset, station, credit, media_type, image)
                VALUES (?, ?, ?, 'image/jpeg', ?)
                ON CONFLICT(dataset, station) DO UPDATE SET credit=excluded.credit,
                    media_type=excluded.media_type, image=excluded.image
            """, (dataset_id, station_id, credit, image))

    @app.put('/api/datasets/{dataset_id}/stations/{station_id}/photo')
    async def put_photo(dataset_id: str, station_id: str, request: Request,
                        credit: str = Query(default='', max_length=160), user=Depends(actor)):
        store = current_store()
        with store.connect() as db:
            check_editor(db, dataset_id, station_id, user)
        media_type = request.headers.get('content-type', '').partition(';')[0].strip().lower()
        if media_type not in FORMATS:
            raise HTTPException(415, 'Выберите JPEG, PNG или WebP')
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BYTES:
                raise HTTPException(413, 'Фотография должна быть не более 5 МиБ')
            body.extend(chunk)
        credit = credit.strip()
        if credit and not credit.isprintable():
            raise HTTPException(422, 'Укажите автора фотографии одной строкой')
        image = await run_in_threadpool(normalized_jpeg, body, FORMATS[media_type])
        await run_in_threadpool(save_photo, store, user, dataset_id, station_id, credit, image)
        return {'photo_url': station_photo_url(dataset_id, station_id), 'photo_credit': credit}
