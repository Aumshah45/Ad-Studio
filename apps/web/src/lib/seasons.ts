// Suggestions for the free-text season field. The API resolves the text deterministically
// (months, named seasons, holidays; services/api/.../data/seasons.yaml) and answers 422
// `unknown-season` for anything it can't read, so this list only helps people type.

export type SeasonKind = "month" | "season" | "holiday";

export interface SeasonSuggestion {
  label: string;
  kind: SeasonKind;
}

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const SEASONS = ["Summer", "Autumn", "Winter", "Spring", "Wet season", "Dry season"];

const HOLIDAYS = [
  "Christmas",
  "Holiday season",
  "New Year",
  "Lunar New Year",
  "Valentine's Day",
  "Carnival",
  "Holi",
  "Easter",
  "Songkran",
  "Golden Week",
  "Mother's Day",
  "Father's Day",
  "Fourth of July",
  "Back to school",
  "Mid-Autumn Festival",
  "Oktoberfest",
  "Diwali",
  "Halloween",
  "Día de Muertos",
  "Thanksgiving",
  "Singles' Day",
  "Black Friday",
  "Hanukkah",
  "Ramadan",
  "Eid",
];

export const SEASON_SUGGESTIONS: readonly SeasonSuggestion[] = [
  ...MONTHS.map((label) => ({ label, kind: "month" as const })),
  ...SEASONS.map((label) => ({ label, kind: "season" as const })),
  ...HOLIDAYS.map((label) => ({ label, kind: "holiday" as const })),
];

export const SEASON_GROUPS: readonly { value: string; items: SeasonSuggestion[] }[] = [
  { value: "Months", items: SEASON_SUGGESTIONS.filter((s) => s.kind === "month") },
  { value: "Seasons", items: SEASON_SUGGESTIONS.filter((s) => s.kind === "season") },
  { value: "Holidays", items: SEASON_SUGGESTIONS.filter((s) => s.kind === "holiday") },
];

/** Same limit as the API's RunCreate.season. */
export const SEASON_MAX_CHARS = 40;
