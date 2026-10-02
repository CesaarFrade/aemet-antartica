"""
Daylight-Saving Time behaviour — challenge Part 1.

The brief explicitly requires to "confirm the behaviour of your API during the
Daylight-Saving Time (DST)", so both 2024 transitions are pinned down here.

Europe/Madrid switches CET (+01:00) -> CEST (+02:00) at 02:00 local on 31 March
2024, skipping that local hour, and back at 03:00 local on 27 October 2024,
repeating the 02:00-03:00 local hour twice.
"""

# --- DST transition dates and offsets ---
SPRING_FORWARD = "2024-03-31"  # 02:00 CET -> 03:00 CEST, the local 02:00 hour never happens
FALL_BACK = "2024-10-27"  # 03:00 CEST -> 02:00 CET, the local 02:00 hour happens twice
CET = "+01:00"
CEST = "+02:00"

STATION = "Meteo Station Juan Carlos I"


def request_range(client, start, end, station=STATION, **params):
    """Calls the challenge endpoint with the given inclusive UTC bounds."""
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"/api/antartida/datos/fechaini/{start}/fechafin/{end}/estacion/{station}"
    return client.get(f"{url}?{query}" if query else url)


def test_output_always_carries_the_utc_offset(mock_aemet, client):
    """The brief requires the Datetime field to include the offset."""
    response = request_range(client, f"{SPRING_FORWARD}T00:00:00", f"{SPRING_FORWARD}T06:00:00")

    assert response.status_code == 200
    for row in response.json()["data"]:
        assert row["Datetime"].endswith((CET, CEST))


def test_spring_forward_skips_the_nonexistent_local_hour(mock_aemet, client):
    """Local 02:00-03:00 does not exist on 31 March, so no sample may land there."""
    response = request_range(client, f"{SPRING_FORWARD}T00:00:00", f"{SPRING_FORWARD}T06:00:00")
    stamps = [row["Datetime"] for row in response.json()["data"]]

    assert stamps[0] == f"{SPRING_FORWARD}T01:00:00{CET}"
    # The clock runs up to 01:50 and then jumps over the missing hour to 03:00
    assert stamps[5] == f"{SPRING_FORWARD}T01:50:00{CET}"
    assert stamps[6] == f"{SPRING_FORWARD}T03:00:00{CEST}"
    assert not any(s.startswith(f"{SPRING_FORWARD}T02:") for s in stamps)


def test_spring_forward_switches_the_offset_mid_dataset(mock_aemet, client):
    """Samples before the transition are CET and samples after it are CEST."""
    response = request_range(client, f"{SPRING_FORWARD}T00:00:00", f"{SPRING_FORWARD}T06:00:00")
    stamps = [row["Datetime"] for row in response.json()["data"]]

    assert any(s.endswith(CET) for s in stamps)
    assert any(s.endswith(CEST) for s in stamps)
    # CET must come first, i.e. the dataset crosses the transition once
    assert next(i for i, s in enumerate(stamps) if s.endswith(CEST)) == len(
        [s for s in stamps if s.endswith(CET)]
    )


def test_spring_forward_hourly_aggregation_has_no_phantom_bucket(mock_aemet, client):
    """Resampling must not invent a bucket for the hour that does not exist."""
    response = request_range(
        client, f"{SPRING_FORWARD}T00:00:00", f"{SPRING_FORWARD}T06:00:00", aggregation="Hourly"
    )
    buckets = [row["Datetime"] for row in response.json()["data"]]

    assert f"{SPRING_FORWARD}T02:00:00{CET}" not in buckets
    assert f"{SPRING_FORWARD}T02:00:00{CEST}" not in buckets
    assert f"{SPRING_FORWARD}T01:00:00{CET}" in buckets
    assert f"{SPRING_FORWARD}T03:00:00{CEST}" in buckets


def test_spring_forward_daily_bins_follow_madrid_not_utc(mock_aemet, client):
    """
    A single UTC day covers two Madrid days, because the station is two hours ahead
    of UTC and the clock moves in between. Binning on UTC would report one day;
    binning on station-local midnight reports 31 March and 1 April, each labelled
    with the offset in force at its own midnight.
    """
    response = request_range(
        client, f"{SPRING_FORWARD}T00:00:00", f"{SPRING_FORWARD}T23:00:00", aggregation="Daily"
    )
    rows = response.json()["data"]

    assert len(rows) == 2
    assert rows[0]["Datetime"] == f"{SPRING_FORWARD}T00:00:00{CET}"  # midnight, still CET
    assert rows[1]["Datetime"] == "2024-04-01T00:00:00+02:00"  # midnight, already CEST


def test_fall_back_keeps_both_passages_of_the_repeated_hour(mock_aemet, client):
    """
    On 27 October the local 02:00 hour happens twice. Dropping either passage
    would silently lose an hour of observations, so both must be reported, and the
    offset is the only thing that tells them apart.
    """
    response = request_range(client, f"{FALL_BACK}T00:00:00", f"{FALL_BACK}T06:00:00")
    stamps = [row["Datetime"] for row in response.json()["data"]]

    assert f"{FALL_BACK}T02:00:00{CEST}" in stamps  # first passage, still summer time
    assert f"{FALL_BACK}T02:00:00{CET}" in stamps  # second passage, back to winter time
    assert stamps.index(f"{FALL_BACK}T02:00:00{CEST}") < stamps.index(f"{FALL_BACK}T02:00:00{CET}")


def test_fall_back_hourly_aggregation_separates_the_repeated_hour(mock_aemet, client):
    """
    Two buckets share the label 02:00, so they must be told apart by their offset
    instead of collapsing into one.
    """
    response = request_range(
        client, f"{FALL_BACK}T00:00:00", f"{FALL_BACK}T06:00:00", aggregation="Hourly"
    )
    buckets = [row["Datetime"] for row in response.json()["data"]]

    assert buckets.count(f"{FALL_BACK}T02:00:00{CEST}") == 1
    assert buckets.count(f"{FALL_BACK}T02:00:00{CET}") == 1
    assert len(buckets) == 7  # 00:00..05:00 UTC is 6 hours, and 02:00 local counts twice


def test_fall_back_daily_bins_follow_madrid_not_utc(mock_aemet, client):
    """
    Same reasoning in autumn: 27 October is still CEST at midnight while
    28 October has already fallen back to CET, so consecutive daily buckets carry
    different offsets.
    """
    response = request_range(
        client, f"{FALL_BACK}T00:00:00", f"{FALL_BACK}T23:00:00", aggregation="Daily"
    )
    rows = response.json()["data"]

    assert len(rows) == 2
    assert rows[0]["Datetime"] == f"{FALL_BACK}T00:00:00{CEST}"
    assert rows[1]["Datetime"] == "2024-10-28T00:00:00+01:00"


def test_monthly_bins_carry_the_offset_of_their_own_month(mock_aemet, client):
    """
    March 2024 closes on CET and April 2024 on CEST, so each monthly bucket must
    report the offset in force for the month it represents.
    """
    response = request_range(
        client, "2024-03-30T00:00:00", "2024-04-02T00:00:00", aggregation="Monthly"
    )
    buckets = [row["Datetime"] for row in response.json()["data"]]

    assert buckets == ["2024-03-31T00:00:00+01:00", "2024-04-30T00:00:00+02:00"]