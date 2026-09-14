export const EXPRESSION_NAMES = [
  "idle",
  "agree",
  "pleased",
  "curious",
  "concerned",
  "surprised",
  "embarrassed",
  "mischievous",
] as const;

export type ExpressionName = (typeof EXPRESSION_NAMES)[number];
export type OfferExpressionName = Exclude<ExpressionName, "idle">;

export const EXPRESSION_GUIDANCE = `Choose exactly one XC Body expression:
- idle: neutral, calm, or purely factual
- agree: agreement, confirmation, or acknowledgment
- pleased: warmth, thanks, success, or good news
- curious: questions, inquiry, or uncertainty
- concerned: warnings, bad news, caution, or empathy
- surprised: genuinely unexpected information
- embarrassed: admitting a mistake or mild self-consciousness
- mischievous: playful, teasing, or knowing humor`;

export function isExpressionName(value: unknown): value is ExpressionName {
  return (
    typeof value === "string" &&
    (EXPRESSION_NAMES as readonly string[]).includes(value)
  );
}

export function isOfferExpressionName(
  value: unknown,
): value is OfferExpressionName {
  return value !== "idle" && isExpressionName(value);
}
