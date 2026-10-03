from fastapi import APIRouter, Query, HTTPException, Depends
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Literal, Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Relative imports to the sibling layers
from services.aemet_client import fetch_aemet_data
from services.data_processor import process_weather_data
from models.database import SessionLocal, MeteoRecord, StationCacheState, StationCoverage
from core.config import CACHE_TTL_MINUTES
from core.exceptions import ConfigurationError, UpstreamAEMETError
from core.logger import get_logger
from core.stations import canonical_station_id, get_all_stations

logger = get_logger("routes")
router = APIRouter()

# The only variable types the source API publishes, per the challenge brief.
# Selecting zero of them returns all of them.
ALLOWED_DATA_TYPES = {"temperature", "pressure", "speed"}

# Cadence at which AEMET publishes observations. Every cache decision is taken on
# grid-aligned instants, so a request for 23:59:59 and one for 23:50 address the
# same last sample instead of the first one refetching the day on every call.
GRID_MINUTES = 10


def _resolve_input_timezone(location: Optional[str]) -> timezone:
    """
    Resolves the `location` parameter into the zone the submitted datetimes are in.

    The brief accepts either an IANA zone (`Europe/Berlin`) or a fixed offset
    (`+02:00`), so both are supported. An absent value means UTC, which is what
    AEMET publishes on and therefore the assumption the API always used.

    Raises:
        HTTPException: 400 for anything that is neither form. Guessing here would
            silently shift a request by hours, so an unrecognised value is refused
            instead of defaulting.
    """
    if not location:
        return timezone.utc

    candidate = location.strip()

    if candidate.startswith(("+", "-")):
        # The sign is parsed separately so the magnitude can be range-checked;
        # `int("-05:30".split(":")[0])` would otherwise yield -5 and fail the
        # 0..23 bound that a valid offset has to satisfy.
        negative = candidate.startswith("-")
        try:
            hours, minutes = (int(part) for part in candidate[1:].split(":"))
        except ValueError:
            hours = minutes = None
        if hours is None or not 0 <= hours <= 23 or not 0 <= minutes <= 59:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid location offset: '{location}'. "
                    "Expected an IANA zone (e.g. Europe/Berlin) or a UTC offset "
                    "in the form +/-HH:MM (e.g. +02:00)."
                ),
            )
        # A negative offset negates the whole magnitude, so -05:30 is five and a
        # half hours *behind* UTC rather than five and a half hours ahead.
        sign = -1 if negative else 1
        return timezone(sign * timedelta(hours=hours, minutes=minutes))

    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown time zone: '{location}'. "
                "Expected an IANA zone (e.g. Europe/Berlin) or a UTC offset "
                "in the form +/-HH:MM (e.g. +02:00)."
            ),
        )


def _utcnow() -> datetime:
    """
    Current UTC wall-clock as a naive datetime.

    The rest of the pipeline handles naive UTC timestamps, so freshness stamps are
    stored the same way to keep every comparison in a single frame of reference.
    `datetime.now(timezone.utc)` avoids the deprecated `utcnow()`.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_grid(dt: datetime) -> datetime:
    """
    Snaps a requested bound onto the 10-minute grid AEMET publishes on.

    Without this, a request ending at 23:59:59 never matches the last sample of
    the day (23:50) and the cache is refetched on every single call. Flooring is
    the right direction for the end bound; for the start bound it is marginally
    lenient, which is harmless while AEMET only emits grid-aligned timestamps.
    """
    return dt.replace(second=0, microsecond=0) - timedelta(minutes=dt.minute % GRID_MINUTES)


def _coverage_spans(db: Session, station_id: str) -> List[tuple]:
    """The station's verified windows, ascending. See `StationCoverage`."""
    return [
        (row.start_ts, row.end_ts)
        for row in db.query(StationCoverage)
        .filter(StationCoverage.station_id == station_id)
        .order_by(StationCoverage.start_ts)
        .all()
    ]


def _first_uncovered(spans: List[tuple], start: datetime, end: datetime):
    """
    Earliest grid instant of [start, end] not inside any recorded span.

    Returns None when the whole window is covered by the spans taken together.
    Spans only need to cover the window between them for it to count: a hole in the
    middle is exactly what this is looking for, and the caller refetches from the
    returned instant onwards, which closes every later hole in the same call.
    """
    step = timedelta(minutes=GRID_MINUTES)
    cursor = start
    for span_start, span_end in spans:
        if span_start > cursor:
            return cursor  # gap begins here
        cursor = max(cursor, span_end + step)
        if cursor > end:
            return None  # the window runs out while still covered
    return None if cursor > end else cursor


def _record_coverage(
    db: Session,
    station_id: str,
    start: datetime,
    end: datetime,
    slots: int,
) -> None:
    """
    Folds [start, end] into the station's spans, keeping them disjoint.

    Overlapping and grid-adjacent windows fuse, so a station fetched day by day
    ends up with one span instead of thousands, and "one row contains the window"
    stays meaningful. Rows intersecting the merged span are dropped rather than
    updated in place, because a merge can move either boundary in either
    direction.
    """
    step = timedelta(minutes=GRID_MINUTES)
    merged_start, merged_end = start, end
    for span_start, span_end in _coverage_spans(db, station_id):
        if span_end >= merged_start - step and span_start <= merged_end + step:
            merged_start = min(merged_start, span_start)
            merged_end = max(merged_end, span_end)

    db.query(StationCoverage).filter(
        StationCoverage.station_id == station_id,
        StationCoverage.start_ts <= merged_end,
        StationCoverage.end_ts >= merged_start,
    ).delete(synchronize_session=False)
    db.add(
        StationCoverage(
            station_id=station_id,
            start_ts=merged_start,
            end_ts=merged_end,
            slots=slots,
        )
    )


def _rows_to_raw(db: Session, station_id: str, start: datetime, end: datetime) -> List[dict]:
    """
    Reads stored rows back into the AEMET field names the processor consumes.

    This is what makes a partially refilled window answer correctly: only the
    missing tail is fetched upstream, so the head that was already cached has to
    come back out of the database for the response to cover the whole window. It
    also gives the cache-hit and cache-miss paths one single mapping, which is why
    both answer the same shape for the same request.
    """
    records = (
        db.query(MeteoRecord)
        .filter(
            MeteoRecord.station_id == station_id,
            MeteoRecord.timestamp >= start,
            MeteoRecord.timestamp <= end,
        )
        .order_by(MeteoRecord.timestamp)
        .all()
    )

    raw = []
    for record in records:
        restored = {
            "fhora": record.timestamp.strftime("%Y-%m-%dT%H:%M:%SUTC"),
            "temp": record.temperature,
            "pres": record.pressure,
            "vel": record.speed,
        }
        # `nombre` is only replayed when a label was actually stored, because the
        # source path produces no `Station` column at all when the payload carries
        # no label. Emitting one here would make a cache hit differ from a cache
        # miss for the same request.
        if record.station_name:
            restored["nombre"] = record.station_name
        raw.append(restored)
    return raw


def _parse_source_timestamp(item: dict) -> Optional[datetime]:
    """
    Reads the `fhora` field of an AEMET row as a naive UTC datetime.

    Returns None for a row whose timestamp is missing or malformed, so the caller
    can drop it without having to distinguish "unparseable" from "out of range".
    """
    raw = item.get("fhora")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.replace("UTC", "").replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None

# --- DATABASE DEPENDENCY ---
def get_db():
    """
    Yields a SQLAlchemy session scoped to a single request.

    FastAPI resolves this through `Depends`, so one session is opened per request
    and always released in the `finally` block, never leaking connections.
    The test suite overrides this dependency with an in-memory database.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/api/antartida/estaciones")
def list_stations():
    """
    Lists the Antarctic stations the service knows about.

    Supporting endpoint for the dashboard, so a client can populate a station picker
    instead of hardcoding identifiers. It reads the curated registry only and never
    touches the cache or AEMET, so it stays available even when the upstream is down.

    Returns:
        `{"status": "success", "data": [{"id": ..., "name": ...}, ...]}`.
    """
    return {
        "status": "success",
        "data": get_all_stations(),
    }


@router.get("/api/antartida/datos/fechaini/{fechaIniStr}/fechafin/{fechaFinStr}/estacion/{identificacion}")
def get_meteo_data(
    fechaIniStr: str,
    fechaFinStr: str,
    identificacion: str,
    location: Optional[str] = Query(None, description="Time zone location of the input dates, e.g., Europe/Berlin or +02:00"),
    aggregation: Literal["None", "Hourly", "Daily", "Monthly"] = Query("None"),
    data_types: Optional[List[str]] = Query(None, description="List of required data types: temperature, pressure, speed"),
    db: Session = Depends(get_db)
):
    """
    Retrieves the time series of measurements for one station within a date range.

    Args:
        fechaIniStr: Inclusive range start, `YYYY-MM-DDTHH:MM:SS`, optional UTC suffix.
        fechaFinStr: Inclusive range end, same format.
        identificacion: Station name or AEMET station code.
        location: Zone the submitted datetimes are expressed in, as an IANA name
            (`Europe/Berlin`) or a fixed offset (`+02:00`). Defaults to UTC, which
            is what AEMET publishes on. Whatever the input zone is, the output is
            always Europe/Madrid, as the brief requires.
        aggregation: `None` keeps the native 10-minute granularity. `Hourly`,
            `Daily` and `Monthly` average the numeric variables on local
            Europe/Madrid boundaries.
        data_types: Any combination of `temperature`, `pressure` and `speed`.
            When omitted or empty, every available variable is returned.

    Returns:
        The requested station, the applied filter and the dataset, where `Datetime`
        is Europe/Madrid (CET/CEST) including its UTC offset.

    Raises:
        HTTPException: 400 when a date is malformed, the range is inverted, or
            `location` is neither a known IANA zone nor a `+/-HH:MM` offset.
            422 when `aggregation` or `data_types` are outside the allowed values.
            502 when the AEMET upstream fails; an upstream outage is never
            reported as an empty station.
    """
    # --- DATA TYPES VALIDATION ---
    # Rejecting unknown names up front matters because the processor treats
    # `data_types` as a whitelist: a name it does not recognise is not an error
    # there, it just silently contributes no column. Validating here is what turns
    # a typo into a 422 instead of a 200 that quietly drops every measurement.
    if data_types:
        invalid = [dt for dt in data_types if dt not in ALLOWED_DATA_TYPES]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid data_types: {invalid}. "
                    f"Allowed values are: {sorted(ALLOWED_DATA_TYPES)}"
                ),
            )

    # --- DATE VALIDATION ---
    # The submitted instants are naive, so `location` is what gives them an
    # absolute meaning. They are normalised to naive UTC here because that is the
    # single frame of reference used by the cache probe, the SQLite rows and the
    # outgoing AEMET call; the CET/CEST rendering happens later, in the processor.
    try:
        clean_ini = fechaIniStr.replace("UTC", "").replace("Z", "")
        clean_fin = fechaFinStr.replace("UTC", "").replace("Z", "")

        # Strict format: anything else is rejected rather than guessed
        dt_ini_naive = datetime.strptime(clean_ini, "%Y-%m-%dT%H:%M:%S")
        dt_fin_naive = datetime.strptime(clean_fin, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid date format. Expected format: YYYY-MM-DDTHH:MM:SS "
                "(e.g., 2024-01-01T00:00:00UTC)"
            ),
        )

    # NOTE: this runs outside the `try` above on purpose. `HTTPException` does not
    # inherit from `ValueError`, so a 400 raised inside it would propagate
    # untouched, but keeping the parse isolated makes that guarantee obvious.
    if dt_ini_naive >= dt_fin_naive:
        raise HTTPException(
            status_code=400,
            detail="Invalid date range: The start date must be strictly before the end date.",
        )

    input_tz = _resolve_input_timezone(location)

    # Both bounds are localised against the requested zone and then flattened to
    # naive UTC, which is what every downstream comparison expects.
    dt_ini = dt_ini_naive.replace(tzinfo=input_tz).astimezone(timezone.utc).replace(tzinfo=None)
    dt_fin = dt_fin_naive.replace(tzinfo=input_tz).astimezone(timezone.utc).replace(tzinfo=None)

    # A zone shift can invert the range even when the naive comparison passed,
    # e.g. `location=-05:00` on a range whose start and end share the same hour.
    if dt_ini >= dt_fin:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid date range: after applying location='"
                f"{location}' the start date is not before the end date."
            ),
        )
    # --- REQUEST LOGGING ---
    # The challenge accepts either the station name or its AEMET code, but both
    # spell the same physical station. Everything downstream (cache key, coverage
    # probe, AEMET request) uses the canonical code, so a station is stored once
    # instead of once per spelling. `station_requested` still echoes the raw input.
    station_key = canonical_station_id(identificacion)
    logger.info(
        f"Incoming request for station: '{identificacion}' (resolved: '{station_key}') "
        f"from {dt_ini} to {dt_fin}"
    )

    # --- CACHE COVERAGE AND FRESHNESS ---
    # A request is served from SQLite only when both conditions hold:
    #
    #   1. some recorded coverage span contains the whole requested window, and
    #   2. the station was refreshed recently enough.
    #
    # Coverage alone would freeze a window forever once filled, which is unusable
    # for the intraday traders this service targets: AEMET revises historical data
    # several times a day, and "the last 3 hours" must keep moving. The freshness
    # stamp bounds the upstream cost to one call per station per TTL window while
    # guaranteeing no hit is ever older than CACHE_TTL_MINUTES.
    #
    # Coverage is read from `StationCoverage` rather than from
    # MIN/MAX(timestamp). The extremes collapse every window a station was ever
    # asked about into one range, so two spans a day apart left the space between
    # them looking cached; combined with freshness being per station rather than
    # per window, such a hole was served as a fresh hit and never refilled. The
    # spans keep the gaps visible, and `_first_uncovered` turns each one into the
    # exact instant a refill has to start from.
    grid_ini = _to_grid(dt_ini)
    grid_fin = _to_grid(dt_fin)
    spans = _coverage_spans(db, station_key)
    uncovered_from = _first_uncovered(spans, grid_ini, grid_fin)

    # Freshness test: the station's last successful refresh must be recent.
    # A station with covered rows but no state row predates this table and is
    # treated as stale, so the first request after upgrading repopulates it.
    state = db.query(StationCacheState).filter(
        StationCacheState.station_id == station_key
    ).first()
    fresh_until = _utcnow() - timedelta(minutes=CACHE_TTL_MINUTES)
    is_fresh = state is not None and state.last_fetched_at > fresh_until

    if uncovered_from is not None:
        logger.info(
            f"CACHE MISS for '{station_key}': coverage incomplete from "
            f"{uncovered_from} (spans: {spans}). Fetching from AEMET API..."
        )
    elif not is_fresh:
        logger.info(
            f"CACHE MISS for '{station_key}': cache expired"
            f"{'' if state else ' (no freshness stamp)'}. Fetching from AEMET API..."
        )
    else:
        logger.info(
            f"CACHE HIT: '{station_key}' covers {dt_ini}..{dt_fin} and was refreshed at "
            f"{state.last_fetched_at} (TTL {CACHE_TTL_MINUTES} min)."
        )

    is_cache_hit = uncovered_from is None and is_fresh

    # --- REFILL RANGE ---
    # Only the missing part of the window is requested upstream. Refetching the
    # whole span just to fill its tail is the common case for a rolling "last N
    # hours" query, where most of the window is already cached and valid, and the
    # upstream call is the expensive part. Everything from the first hole to the
    # end of the window is taken in one request so that several holes close at once.
    #
    # An expired station refetches end to end regardless of what is already cached:
    # AEMET revises historical values, so refreshing only the tail would leave the
    # head pinned to a superseded revision indefinitely. Freshness wins over the
    # gap, because a revision is a silent wrong answer while a hole is at least
    # visible.
    refill_ini = (
        uncovered_from if (uncovered_from is not None and is_fresh) else grid_ini
    )
    refill_fin = grid_fin

    raw_data = []

    # --- CACHE HIT ---
    if is_cache_hit:
        raw_data = _rows_to_raw(db, station_key, dt_ini, dt_fin)

    # --- CACHE MISS ---
    else:
        # AEMET expects the UTC suffix to be part of the path parameter
        aemet_ini = refill_ini.strftime("%Y-%m-%dT%H:%M:%SUTC")
        aemet_fin = refill_fin.strftime("%Y-%m-%dT%H:%M:%SUTC")

        # An unreachable or erroring upstream is a gateway failure, not an empty
        # station. Answering 502 keeps a source-API outage distinguishable from a
        # window that genuinely holds no observations, so callers do not read an
        # outage as "the station published nothing" and silently keep the cache.
        try:
            raw_data = fetch_aemet_data(aemet_ini, aemet_fin, station_key)
        except ConfigurationError as error:
            # This deployment cannot talk to AEMET at all. That is a local
            # misconfiguration, not an upstream outage, so it must not be reported
            # as 502 or be cached around.
            logger.error(f"AEMET client is not configured: {error}")
            raise HTTPException(status_code=503, detail=str(error))
        except UpstreamAEMETError as error:
            logger.error(f"AEMET upstream failure for {station_key}: {error}")
            raise HTTPException(
                status_code=502,
                detail=f"Upstream AEMET API unavailable: {error}",
            )

        # AEMET answers with whole days regardless of the window that was asked
        # for, so its payload routinely reaches past both bounds. The trim happens
        # once, here, and feeds both the cache and the response, which is what
        # keeps the two paths answering identical data.
        #
        # Ingesting the out-of-window rows would be wrong twice over: the delete
        # below is scoped to the refill range, so those rows would survive the
        # next refresh and then collide with it on (station_id, timestamp),
        # turning a routine refresh into an IntegrityError.
        fetched_count = len(raw_data)
        raw_data = [
            item for item in raw_data
            if (item_dt := _parse_source_timestamp(item)) is not None
            and refill_ini <= item_dt <= refill_fin
        ]
        out_of_window = fetched_count - len(raw_data)

        if not raw_data:
            # A valid but empty answer: keep whatever the cache already holds for
            # this window instead of treating it as an invalidation. The freshness
            # stamp is deliberately left untouched, because nothing new arrived,
            # and so is the coverage span: nothing was verified, so claiming the
            # window would make the next request a hit over an empty hole.
            logger.warning(
                f"AEMET returned no observations for {station_key} "
                f"between {aemet_ini} and {aemet_fin}. Serving an empty dataset."
            )
        else:
            if out_of_window:
                logger.info(
                    f"AEMET returned {fetched_count} rows for {station_key}, "
                    f"{out_of_window} of them outside {aemet_ini}..{aemet_fin}; "
                    f"ingesting the remaining {len(raw_data)}."
                )

            logger.info("Saving new AEMET data to SQLite cache...")
            db_records_to_insert = []
            # AEMET labels every sample of a station the same way, so the first
            # non-empty label is stored once and reused on every cache hit.
            station_label = next(
                (item.get("nombre") for item in raw_data if item.get("nombre")),
                None,
            )

            for item in raw_data:
                # `raw_data` was already trimmed and validated above, so the
                # timestamp always parses here.
                new_record = MeteoRecord(
                    station_id=station_key,
                    station_name=station_label,
                    timestamp=_parse_source_timestamp(item),
                    temperature=item.get("temp"),
                    pressure=item.get("pres"),
                    speed=item.get("vel")
                )
                db_records_to_insert.append(new_record)

            # Replace whatever we hold for the refill range instead of appending,
            # so a refresh cannot duplicate rows. The delete and the insert share a
            # single transaction, so the swap stays atomic, and both only run once
            # the payload has been parsed: a truncated or failed upstream response
            # must never wipe data we already had.
            #
            # The delete stops at `refill_ini` rather than at the start of the
            # request, so a tail refill leaves the still-valid cached head in place.
            #
            # The unique constraint on (station_id, timestamp) backs this up at the
            # schema level, so even two concurrent refreshes of the same window
            # cannot both insert the same sample.
            db.query(MeteoRecord).filter(
                MeteoRecord.station_id == station_key,
                MeteoRecord.timestamp >= refill_ini,
                MeteoRecord.timestamp <= refill_fin
            ).delete()

            db.add_all(db_records_to_insert)

            # Record the window that was verified. This happens inside the same
            # transaction as the rows, so coverage can never claim a window whose
            # samples were rolled back.
            _record_coverage(db, station_key, refill_ini, refill_fin, len(db_records_to_insert))

            # AEMET publishes on a 10-minute grid, so a window that came back short
            # either lost samples in transit or covers a real sensor gap. Neither is
            # fatal, but silently under-reporting a gap is exactly the kind of thing
            # that should not stay invisible, and `slots` on the span makes it
            # queryable afterwards.
            expected_slots = int(
                (refill_fin - refill_ini).total_seconds() // (GRID_MINUTES * 60)
            ) + 1
            if len(db_records_to_insert) < expected_slots:
                logger.warning(
                    f"AEMET returned {len(db_records_to_insert)} samples for "
                    f"{station_key} over {refill_ini}..{refill_fin}, fewer than the "
                    f"{expected_slots} grid instants in that span. The missing slots "
                    f"are treated as absent upstream, not as a cache defect; the "
                    f"window is refetched end to end once the TTL expires."
                )

            # Only a successful ingestion marks the station fresh. Recording it
            # here, inside the same transaction as the rows, means the stamp can
            # never claim freshness for data that was not actually stored.
            state = db.query(StationCacheState).filter(
                StationCacheState.station_id == station_key
            ).first()
            fetched_at = _utcnow()
            if state:
                state.last_fetched_at = fetched_at
            else:
                db.add(StationCacheState(station_id=station_key, last_fetched_at=fetched_at))

            try:
                db.commit()
            except IntegrityError as error:
                # Losing the race against a concurrent refresh of the same window
                # is not a client error: the other writer stored the very same
                # samples, so the cache ends up exactly as correct as this request
                # wanted it. The rollback is what matters here, because it undoes
                # the delete above and stops a lost race from emptying the window.
                db.rollback()
                logger.warning(
                    f"Concurrent refresh of '{station_key}' for {dt_ini}..{dt_fin} "
                    f"committed first; keeping the stored rows. ({error.orig})"
                )
            else:
                logger.info(
                    f"Successfully cached {len(db_records_to_insert)} records for '{station_key}' "
                    f"and marked it fresh at {fetched_at}."
                )
                # Answer from what is now stored rather than from the payload, so a
                # window that was only partially refilled still comes back whole.
                # The payload alone would report just the tail and quietly drop the
                # cached head the caller also asked for.
                raw_data = _rows_to_raw(db, station_key, dt_ini, dt_fin)
    # --- TRANSFORMATION & RESPONSE ---
    # Column renaming, the Europe/Madrid conversion, the time aggregation and
    # the data_types filter all live in the service layer, keeping this endpoint
    # thin and focused on transport concerns.
    processed_data = process_weather_data(raw_data, data_types, aggregation)

    # `location` describes the zone the request was expressed in; the brief fixes
    # the output to Europe/Madrid, so the two are echoed separately rather than
    # conflated. Both being visible keeps the conversion auditable from outside.
    return {
        "status": "success",
        "station_requested": identificacion,
        "station_resolved": station_key,
        "location_requested": location or "UTC",
        "location_resolved": "Europe/Madrid",
        "data_types_filtered": data_types if data_types else "All",
        "data": processed_data
    }
