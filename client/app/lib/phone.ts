/* ═══════════════════════════════════════════════════════════════════════
   A sample mobile number in international format for the hotel's country,
   for placeholders and validation hints, so a Tokyo front desk is shown a
   Japanese number and a Dubai one a UAE number, never one country's number
   for every hotel. Samples follow each country's mobile format; nobody is
   meant to dial them. A country without a sample gets PHONE_FORMAT_HINT.
   Pure strings: safe in server and client code.
   ═══════════════════════════════════════════════════════════════════════ */

const SAMPLES: Readonly<Record<string, string>> = {
  JP: "+81 90 1234 5678",
  KR: "+82 10 1234 5678",
  CN: "+86 131 2345 6789",
  HK: "+852 5123 4567",
  TW: "+886 912 345 678",
  TH: "+66 81 234 5678",
  SG: "+65 8123 4567",
  ID: "+62 812 3456 7890",
  PH: "+63 917 123 4567",
  VN: "+84 91 234 5678",
  AU: "+61 412 345 678",
  IN: "+91 98765 43210",
  AE: "+971 50 123 4567",
  GB: "+44 7700 900123",
};

/** Said instead of a sample when the country has none. */
export const PHONE_FORMAT_HINT = "+ country code, then the number";

/** "+81 90 1234 5678" for JP; null for a country without a sample. */
export function phoneSample(country?: string | null): string | null {
  return SAMPLES[(country || "").trim().toUpperCase()] ?? null;
}

/** A placeholder for a phone input: the country's sample, else the format. */
export function phonePlaceholder(country?: string | null): string {
  return phoneSample(country) ?? PHONE_FORMAT_HINT;
}

/** "International format, e.g. +81 90 1234 5678." or the format alone. */
export function phoneFormatHint(country?: string | null): string {
  const sample = phoneSample(country);
  return sample ? `International format, e.g. ${sample}.` : "International format: + and the country code, then the number.";
}
