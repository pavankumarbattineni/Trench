/** A tiny, safe arithmetic evaluator for calculator formulas the model
 * writes inside ```trench-ui blocks. Never uses eval/Function: the input
 * is tokenized and parsed by a recursive-descent parser that only knows
 * numbers, variables from `scope`, + - * / ^ ( ) and a fixed set of
 * functions. Anything else throws a FormulaError.
 *
 * Grammar (^ is right-associative and binds tighter than unary minus,
 * so -2^2 = -4):
 *   expr    = term (("+" | "-") term)*
 *   term    = unary (("*" | "/") unary)*
 *   unary   = ("-" | "+") unary | power
 *   power   = primary ("^" unary)?
 *   primary = number | ident | ident "(" expr ("," expr)* ")" | "(" expr ")"
 */

export class FormulaError extends Error {}

const FUNCTIONS: Record<string, (...args: number[]) => number> = {
  min: (...a) => Math.min(...a),
  max: (...a) => Math.max(...a),
  round: (x, digits = 0) => {
    const f = 10 ** Math.max(0, Math.min(10, Math.trunc(digits)));
    return Math.round(x * f) / f;
  },
  abs: Math.abs,
  floor: Math.floor,
  ceil: Math.ceil,
  sqrt: Math.sqrt,
  // Not advertised in the prompt as core, but models reach for them.
  pow: (x, y) => x ** y,
  exp: Math.exp,
  log: Math.log,
  ln: Math.log,
  log10: Math.log10,
};

type Token =
  | { kind: "num"; value: number }
  | { kind: "ident"; name: string }
  | { kind: "op"; op: string };

const MAX_LENGTH = 1000;

function tokenize(source: string): Token[] {
  if (source.length > MAX_LENGTH) throw new FormulaError("Formula is too long");
  const tokens: Token[] = [];
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    if (/\s/.test(ch)) {
      i++;
    } else if (/[0-9.]/.test(ch)) {
      const match = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(source.slice(i));
      if (!match) throw new FormulaError(`Bad number at ${i}`);
      tokens.push({ kind: "num", value: Number(match[0]) });
      i += match[0].length;
    } else if (/[A-Za-z_]/.test(ch)) {
      const match = /^[A-Za-z_][A-Za-z0-9_]*/.exec(source.slice(i))!;
      tokens.push({ kind: "ident", name: match[0] });
      i += match[0].length;
    } else if ("+-*/^(),".includes(ch)) {
      tokens.push({ kind: "op", op: ch });
      i++;
    } else {
      throw new FormulaError(`Unexpected "${ch}"`);
    }
  }
  return tokens;
}

export function evaluateFormula(source: string, scope: Readonly<Record<string, number>>): number {
  const tokens = tokenize(source);
  let pos = 0;
  let depth = 0;

  const peek = () => tokens[pos];
  const isOp = (op: string) => {
    const t = tokens[pos];
    return t?.kind === "op" && t.op === op;
  };
  const expectOp = (op: string) => {
    if (!isOp(op)) throw new FormulaError(`Expected "${op}"`);
    pos++;
  };

  function expr(): number {
    if (++depth > 100) throw new FormulaError("Formula is nested too deeply");
    let value = term();
    while (isOp("+") || isOp("-")) {
      const op = (tokens[pos++] as { op: string }).op;
      const rhs = term();
      value = op === "+" ? value + rhs : value - rhs;
    }
    depth--;
    return value;
  }

  function term(): number {
    let value = unary();
    while (isOp("*") || isOp("/")) {
      const op = (tokens[pos++] as { op: string }).op;
      const rhs = unary();
      value = op === "*" ? value * rhs : value / rhs;
    }
    return value;
  }

  function unary(): number {
    if (isOp("-")) {
      pos++;
      return -unary();
    }
    if (isOp("+")) {
      pos++;
      return unary();
    }
    return power();
  }

  function power(): number {
    const base = primary();
    if (isOp("^")) {
      pos++;
      return base ** unary();
    }
    return base;
  }

  function primary(): number {
    const t = peek();
    if (!t) throw new FormulaError("Unexpected end of formula");
    if (t.kind === "num") {
      pos++;
      return t.value;
    }
    if (t.kind === "ident") {
      pos++;
      if (isOp("(")) {
        const fn = Object.hasOwn(FUNCTIONS, t.name) ? FUNCTIONS[t.name] : undefined;
        if (!fn) throw new FormulaError(`Unknown function "${t.name}"`);
        pos++;
        const args = [expr()];
        while (isOp(",")) {
          pos++;
          args.push(expr());
        }
        expectOp(")");
        return fn(...args);
      }
      if (!Object.hasOwn(scope, t.name)) throw new FormulaError(`Unknown value "${t.name}"`);
      return scope[t.name];
    }
    if (t.op === "(") {
      pos++;
      const value = expr();
      expectOp(")");
      return value;
    }
    throw new FormulaError(`Unexpected "${t.op}"`);
  }

  const result = expr();
  if (pos !== tokens.length) throw new FormulaError("Unexpected text after formula");
  return result;
}
