/** A tiny, safe evaluator for calculator formulas the model writes inside
 * ```trench-ui blocks. Never uses eval/Function: the input is tokenized and
 * parsed by a recursive-descent parser that only knows numbers, variables
 * from `scope`, arithmetic, comparisons and a fixed set of functions.
 * Anything else throws a FormulaError with a message fit to show a user.
 *
 * Grammar (^ is right-associative and binds tighter than unary minus,
 * so -2^2 = -4; comparisons give 1 for true and 0 for false):
 *   compare = expr (("==" | "=" | "!=" | "<" | "<=" | ">" | ">=") expr)?
 *   expr    = term (("+" | "-") term)*
 *   term    = unary (("*" | "/") unary)*
 *   unary   = ("-" | "+") unary | power
 *   power   = primary ("^" unary)?
 *   primary = number | ident | ident "(" compare ("," compare)* ")" | "(" compare ")"
 */

export class FormulaError extends Error {}

const truthy = (x: number) => x !== 0 && !Number.isNaN(x);

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
  pow: (x, y) => x ** y,
  exp: Math.exp,
  log: Math.log,
  ln: Math.log,
  log10: Math.log10,
  // if(condition, then, else) -- a non-zero condition picks `then`.
  if: (cond, a, b) => (truthy(cond) ? a : b),
  and: (...a) => (a.every(truthy) ? 1 : 0),
  or: (...a) => (a.some(truthy) ? 1 : 0),
  not: (x) => (truthy(x) ? 0 : 1),
};

const ARITY: Record<string, [number, number]> = {
  if: [3, 3],
  not: [1, 1],
  abs: [1, 1],
  floor: [1, 1],
  ceil: [1, 1],
  sqrt: [1, 1],
  exp: [1, 1],
  log: [1, 1],
  ln: [1, 1],
  log10: [1, 1],
  pow: [2, 2],
  round: [1, 2],
};

const COMPARISONS: Record<string, (a: number, b: number) => boolean> = {
  "==": (a, b) => a === b,
  "=": (a, b) => a === b,
  "!=": (a, b) => a !== b,
  "<": (a, b) => a < b,
  "<=": (a, b) => a <= b,
  ">": (a, b) => a > b,
  ">=": (a, b) => a >= b,
};

type Token =
  | { kind: "num"; value: number }
  | { kind: "ident"; name: string }
  | { kind: "op"; op: string };

const MAX_LENGTH = 2000;

function tokenize(source: string): Token[] {
  if (source.length > MAX_LENGTH) throw new FormulaError("The formula is too long");
  const tokens: Token[] = [];
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    const two = source.slice(i, i + 2);
    if (/\s/.test(ch)) {
      i++;
    } else if (/[0-9.]/.test(ch)) {
      const match = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(source.slice(i));
      if (!match) throw new FormulaError(`Malformed number in the formula`);
      tokens.push({ kind: "num", value: Number(match[0]) });
      i += match[0].length;
    } else if (/[A-Za-z_]/.test(ch)) {
      const match = /^[A-Za-z_][A-Za-z0-9_]*/.exec(source.slice(i))!;
      tokens.push({ kind: "ident", name: match[0] });
      i += match[0].length;
    } else if (["==", "!=", "<=", ">="].includes(two)) {
      tokens.push({ kind: "op", op: two });
      i += 2;
    } else if ("+-*/^(),<>=".includes(ch)) {
      tokens.push({ kind: "op", op: ch });
      i++;
    } else {
      throw new FormulaError(`The formula uses an unsupported character "${ch}"`);
    }
  }
  return tokens;
}

/** Names a formula refers to as values (not function names). */
export function formulaIdentifiers(source: string): string[] {
  let tokens: Token[];
  try {
    tokens = tokenize(source);
  } catch {
    return [];
  }
  return tokens.flatMap((t, i) => {
    const next = tokens[i + 1];
    return t.kind === "ident" && !(next?.kind === "op" && next.op === "(") ? [t.name] : [];
  });
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
    if (!isOp(op)) throw new FormulaError(`The formula is missing "${op}"`);
    pos++;
  };

  function compare(): number {
    if (++depth > 100) throw new FormulaError("The formula is nested too deeply");
    const lhs = expr();
    const t = tokens[pos];
    let value = lhs;
    if (t?.kind === "op" && Object.hasOwn(COMPARISONS, t.op)) {
      pos++;
      value = COMPARISONS[t.op](lhs, expr()) ? 1 : 0;
    }
    depth--;
    return value;
  }

  function expr(): number {
    let value = term();
    while (isOp("+") || isOp("-")) {
      const op = (tokens[pos++] as { op: string }).op;
      const rhs = term();
      value = op === "+" ? value + rhs : value - rhs;
    }
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
    if (!t) throw new FormulaError("The formula ends unexpectedly");
    if (t.kind === "num") {
      pos++;
      return t.value;
    }
    if (t.kind === "ident") {
      pos++;
      if (isOp("(")) {
        const name = t.name.toLowerCase();
        const fn = Object.hasOwn(FUNCTIONS, name) ? FUNCTIONS[name] : undefined;
        if (!fn) throw new FormulaError(`Uses an unsupported function: ${t.name}()`);
        pos++;
        const args = [compare()];
        while (isOp(",")) {
          pos++;
          args.push(compare());
        }
        expectOp(")");
        const [lo, hi] = ARITY[name] ?? [1, Infinity];
        if (args.length < lo || args.length > hi) {
          throw new FormulaError(`${t.name}() got ${args.length} argument(s)`);
        }
        return fn(...args);
      }
      if (!Object.hasOwn(scope, t.name)) throw new FormulaError(`Refers to an unknown value: ${t.name}`);
      return scope[t.name];
    }
    if (t.op === "(") {
      pos++;
      const value = compare();
      expectOp(")");
      return value;
    }
    throw new FormulaError(`Unexpected "${t.op}" in the formula`);
  }

  const result = compare();
  if (pos !== tokens.length) throw new FormulaError("The formula has unexpected text at the end");
  return result;
}
