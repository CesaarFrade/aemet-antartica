// frontend/src/constants/locations.ts

/**
 * Choices for the `location` parameter of the brief, which states the time zone the
 * submitted datetimes are written in.
 *
 * The endpoint accepts exactly two forms and answers anything else with a 400
 * (see `_resolve_input_timezone` in `backend/api/routes.py`): an IANA zone such as
 * `Europe/Berlin`, or a fixed offset such as `+02:00`. `value` is therefore always
 * sent verbatim, while `label` only exists for the analyst.
 *
 * The list is curated rather than generated from `Intl.supportedValuesOf('timeZone')`
 * on purpose: the ~400 zones it returns would bury the handful an analyst actually
 * needs, and a native `<select>` cannot be filtered. If the full catalogue is ever
 * wanted, this file is the only thing that has to change.
 */

/**
 * Empty means "send no `location` at all". The endpoint reads an absent value as UTC,
 * which is the zone AEMET publishes on, so the default request is byte-for-byte the
 * one the backend has always received.
 */
export const DEFAULT_LOCATION = '';

export interface LocationOption {
  /** Verbatim value of the `location` query parameter. */
  readonly value: string;
  /** What the analyst reads in the dropdown. */
  readonly label: string;
}

export interface LocationGroup {
  readonly label: string;
  readonly options: readonly LocationOption[];
}

export const LOCATION_GROUPS: readonly LocationGroup[] = [
  {
    label: 'Named time zone (IANA)',
    options: [
      { value: DEFAULT_LOCATION, label: 'UTC (AEMET publishes on)' },
      { value: 'Europe/Madrid', label: 'Europe/Madrid (CET/CEST)' },
      { value: 'Europe/Berlin', label: 'Europe/Berlin' },
      { value: 'America/New_York', label: 'America/New_York' },
      { value: 'Asia/Tokyo', label: 'Asia/Tokyo (no DST)' },
      { value: 'Antarctica/Troll', label: 'Antarctica/Troll (station clock)' },
    ],
  },
  {
    label: 'Fixed offset (DST ignored)',
    options: [
      { value: '+02:00', label: '+02:00 (CEST, fixed)' },
      { value: '-05:00', label: '-05:00 (fixed)' },
    ],
  },
];

/** Every option in one flat list, used to resolve the label of the current selection. */
export const LOCATION_OPTIONS: readonly LocationOption[] = LOCATION_GROUPS.flatMap(
  (group) => group.options,
);

/**
 * Human-readable name of a `location` value, for the line under the filter form.
 * An unknown value falls back to itself, which is also what the endpoint would
 * reject, so a stale selection stays visible instead of rendering as blank.
 */
export const describeLocation = (value: string): string =>
  LOCATION_OPTIONS.find((option) => option.value === value)?.label ?? value;
