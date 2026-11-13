"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useTheme } from "next-themes";
import {
  Check,
  CircleAlert,
  CodeXml,
  Copy,
  Download,
  Maximize2,
  Minus,
  Plus,
  RotateCcw,
  Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cleanMermaidSource } from "@/lib/mermaid-source";
import { cn } from "cn";

type Mermaid = typeof import("mermaid").default;

// Mermaid is large, so it's only fetched the first time a message actually
// contains a diagram, then reused for every later one.
let mermaidPromise: Promise<Mermaid> | null = null;
function loadMermaid(): Promise<Mermaid> {
  mermaidPromise ??= import("mermaid").then((mod) => mod.default);
  return mermaidPromise;
}

/** Resolves any CSS color (hex, rgba, ...) to an opaque #rrggbb by painting
 * it over `backdrop` -- Mermaid derives shades from its themeVariables and
 * only understands plain hex reliably. */
function toHex(color: string, backdrop: string): string {
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx || !color) return color;
  ctx.fillStyle = backdrop;
  ctx.fillRect(0, 0, 1, 1);
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return `#${[r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}

/** Mermaid's "base" theme built from the app's own CSS variables, so
 * diagrams follow the current light/dark theme. */
function appThemeVariables(isDark: boolean) {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string) => style.getPropertyValue(name).trim();
  const card = toHex(read("--card"), isDark ? "#000000" : "#ffffff");
  const color = (name: string) => toHex(read(name), card);

  const foreground = color("--foreground");
  const muted = color("--muted");
  const mutedForeground = color("--muted-foreground");
  const accent = color("--accent");
  const border = color("--border");

  return {
    darkMode: isDark,
    background: card,
    fontFamily: getComputedStyle(document.body).fontFamily,
    fontSize: "14px",
    textColor: foreground,
    primaryColor: muted,
    primaryTextColor: foreground,
    primaryBorderColor: mutedForeground,
    secondaryColor: accent,
    secondaryTextColor: foreground,
    secondaryBorderColor: mutedForeground,
    tertiaryColor: card,
    tertiaryTextColor: foreground,
    tertiaryBorderColor: border,
    mainBkg: muted,
    nodeBorder: mutedForeground,
    nodeTextColor: foreground,
    lineColor: mutedForeground,
    clusterBkg: accent,
    clusterBorder: border,
    edgeLabelBackground: card,
    titleColor: foreground,
    noteBkgColor: accent,
    noteTextColor: foreground,
    noteBorderColor: border,
    actorBkg: muted,
    actorBorder: mutedForeground,
    actorTextColor: foreground,
    signalColor: foreground,
    signalTextColor: foreground,
  };
}

/** Mermaid emits `width="100%"` plus an inline max-width, which shrinks a
 * wide diagram to the column width until its text is unreadable. Pin the
 * SVG to its natural (viewBox) size instead; the container scrolls. */
function atNaturalSize(svg: string): string {
  const viewBox = svg.match(/viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"/);
  if (!viewBox) return svg;
  const [, width, height] = viewBox;
  return svg.replace(/<svg([^>]*)>/, (_match, attrs: string) => {
    const cleaned = attrs
      .replace(/\swidth="[^"]*"/, "")
      .replace(/\sheight="[^"]*"/, "")
      .replace(/max-width:\s*[\d.]+px;?/, "");
    return `<svg${cleaned} width="${Math.ceil(Number(width))}" height="${Math.ceil(Number(height))}">`;
  });
}

/** Grows a rendered diagram's frame (viewBox, width, height) to everything
 * it actually drew, then sizes it at `zoom`. Mermaid frames the diagram with
 * text measured while drawing; when the browser then renders text larger
 * (a minimum font size, a page-altering extension), labels and nodes spilled
 * past the frame and were clipped -- and clipped content never produces a
 * scrollbar. Never crops: the frame only grows. */
function fitToContent(svg: SVGSVGElement, zoom: number): void {
  const box = svg.getBBox();
  if (!box.width || !box.height) return;
  const pad = 8;
  const [vx = 0, vy = 0, vw = 0, vh = 0] = (svg.getAttribute("viewBox") ?? "")
    .trim()
    .split(/\s+/)
    .map(Number);
  const x = Math.min(vx, box.x - pad);
  const y = Math.min(vy, box.y - pad);
  const width = Math.max(vx + vw, box.x + box.width + pad) - x;
  const height = Math.max(vy + vh, box.y + box.height + pad) - y;
  svg.setAttribute("viewBox", `${x} ${y} ${width} ${height}`);
  svg.setAttribute("width", String(Math.ceil(width * zoom)));
  svg.setAttribute("height", String(Math.ceil(height * zoom)));
}

/** First line of a Mermaid parse error, which is the human-readable part
 * ("Parse error on line 3: ..."); the rest is a caret diagram. */
function summarizeError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  return message.split("\n")[0]?.trim() || "Invalid diagram syntax";
}

const ZOOM_MIN = 0.5;
const ZOOM_MAX = 3;
const ZOOM_STEP = 0.25;

type RenderState =
  | { status: "waiting" } // answer still streaming -- source incomplete
  | { status: "rendering" }
  | { status: "ready"; svg: string; source: string }
  | { status: "error"; message: string };

interface MermaidDiagramProps {
  /** The Mermaid source from a ```mermaid fenced block, as the model wrote it. */
  code: string;
  /** While the answer is still streaming the source is usually incomplete
   * (and unparseable), so rendering waits until the message finishes. */
  isStreaming?: boolean;
}

export function MermaidDiagram({ code, isStreaming = false }: MermaidDiagramProps) {
  const { resolvedTheme } = useTheme();
  const reactId = useId();
  const [view, setView] = useState<"diagram" | "code">("diagram");
  const [state, setState] = useState<RenderState>({ status: "rendering" });
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [zoom, setZoom] = useState(1);
  const scratchRef = useRef<HTMLDivElement>(null);
  const inlineRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isStreaming) return;
    let cancelled = false;

    (async () => {
      const original = code.trim();
      const cleaned = cleanMermaidSource(original);
      try {
        const mermaid = await loadMermaid();
        // Mermaid sizes each label from measured text; measuring before the
        // web font arrives gives boxes too small for the final text, which
        // then clips.
        await document.fonts?.ready;
        mermaid.initialize({
          startOnLoad: false,
          // No click handlers, scripts or raw HTML from model-written source.
          securityLevel: "strict",
          suppressErrorRendering: true,
          // Flat style: Mermaid 12's default "neo" look adds a fixed
          // light-grey drop shadow that glows on the dark theme.
          look: "classic",
          // Labels as SVG <text>, not HTML in <foreignObject>: HTML labels
          // were measured before layout settled (clipped text), and some
          // browsers/extensions failed to paint them in large diagrams,
          // leaving edges pointing at empty space.
          htmlLabels: false,
          flowchart: { htmlLabels: false },
          theme: "base",
          themeVariables: appThemeVariables(resolvedTheme === "dark"),
        });

        // Prefer the cleaned source; fall back to the original in case the
        // cleanup itself is what a valid-but-unusual diagram trips on.
        let source = cleaned;
        let parseError: unknown = null;
        for (const candidate of cleaned === original ? [original] : [cleaned, original]) {
          try {
            await mermaid.parse(candidate);
            source = candidate;
            parseError = null;
            break;
          } catch (err) {
            parseError ??= err;
          }
        }
        if (parseError) throw parseError;

        // Unique, CSS-safe id per render (useId's ":r0:" isn't a valid id
        // selector, and re-rendering on theme change needs a fresh one).
        const renderId = `mermaid-${reactId.replace(/[^a-zA-Z0-9-]/g, "")}-${Date.now()}`;
        // Mermaid measures text in a temporary element; by default it's
        // appended to <body>, which briefly stretches the page. Draw in a
        // clipped scratch element inside this card instead.
        const { svg } = await mermaid.render(
          renderId,
          source,
          scratchRef.current ?? undefined
        );
        if (!cancelled) setState({ status: "ready", svg: atNaturalSize(svg), source });
      } catch (err) {
        if (!cancelled) setState({ status: "error", message: summarizeError(err) });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code, isStreaming, resolvedTheme, reactId]);

  const effective: RenderState = isStreaming ? { status: "waiting" } : state;
  const readySvg = effective.status === "ready" ? effective.svg : null;

  // Fit each displayed copy to what the browser actually drew (see
  // fitToContent). DOM-only: the SVG markup itself stays as Mermaid made it.
  useLayoutEffect(() => {
    const svg = inlineRef.current?.querySelector("svg");
    if (svg) fitToContent(svg, 1);
  }, [readySvg, view]);
  useLayoutEffect(() => {
    if (!expanded) return;
    const fit = () => {
      const svg = dialogRef.current?.querySelector("svg");
      if (svg) fitToContent(svg, zoom);
      return !!svg;
    };
    // The dialog's content can mount a frame after it opens.
    if (!fit()) {
      const frame = requestAnimationFrame(fit);
      return () => cancelAnimationFrame(frame);
    }
  }, [readySvg, expanded, zoom]);
  // Copy/download hand out the source that actually rendered, so pasting
  // it into another Mermaid tool works; otherwise the model's original.
  const shownSource = effective.status === "ready" ? effective.source : code.trim();
  const showCode = view === "code" || effective.status === "waiting";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shownSource);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be unavailable (permissions/insecure context).
    }
  };

  const handleDownload = () => {
    if (effective.status !== "ready") return;
    // The on-screen copy, so the download carries the corrected frame.
    const shown = inlineRef.current?.querySelector("svg");
    const markup = shown
      ? new XMLSerializer().serializeToString(shown)
      : effective.svg;
    const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "diagram.svg";
    link.click();
    URL.revokeObjectURL(url);
  };

  const openExpanded = () => {
    setZoom(1);
    setExpanded(true);
  };

  return (
    // font-sans: the card's buttons and labels are UI (Inter), even though
    // the card sits inside the serif response text.
    <div className="relative mb-3 overflow-hidden rounded-lg border border-border font-sans last:mb-0">
      {/* Off-screen scratch space Mermaid measures/draws in (see render()). */}
      <div
        ref={scratchRef}
        aria-hidden
        className="pointer-events-none invisible absolute top-0 left-0 h-0 w-0 overflow-hidden"
      />
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted px-2 py-1.5">
        <div role="group" aria-label="Show as" className="inline-flex gap-0.5">
          {(
            [
              ["diagram", "Diagram", Workflow],
              ["code", "Code", CodeXml],
            ] as const
          ).map(([value, label, Icon]) => {
            const active = (value === "code") === showCode;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={active}
                onClick={() => setView(value)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-background hover:text-foreground"
                )}
              >
                <Icon className="size-3.5" />
                {label}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-0.5">
          {effective.status === "ready" && !showCode && (
            <>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Download as SVG"
                title="Download as SVG"
                onClick={handleDownload}
              >
                <Download className="size-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Open full size"
                title="Open full size"
                onClick={openExpanded}
              >
                <Maximize2 className="size-3.5" />
              </Button>
            </>
          )}
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={copied ? "Copied" : "Copy Mermaid code"}
            title={copied ? "Copied" : "Copy Mermaid code"}
            onClick={() => void handleCopy()}
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          </Button>
        </div>
      </div>

      {effective.status === "waiting" && (
        <p className="border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
          The diagram appears when the answer finishes.
        </p>
      )}

      {showCode ? (
        <pre className="overflow-x-auto bg-card p-3 font-mono text-[0.875em]">
          <code>{shownSource}</code>
        </pre>
      ) : effective.status === "error" ? (
        <div className="flex flex-col items-start gap-2 bg-card p-4 text-sm">
          <p className="flex items-center gap-1.5 font-medium">
            <CircleAlert className="size-4 text-destructive" />
            This diagram can&apos;t be drawn
          </p>
          <p className="text-xs text-muted-foreground">
            Its Mermaid code has a syntax error: {effective.message}
          </p>
          <Button variant="outline" size="sm" onClick={() => setView("code")}>
            <CodeXml className="size-3.5" />
            Show code
          </Button>
        </div>
      ) : effective.status === "ready" ? (
        // Natural size; a diagram wider than the column scrolls sideways
        // (mx-auto centers narrow ones without clipping wide ones).
        <div className="max-h-[32rem] overflow-auto bg-card p-4">
          <button
            ref={inlineRef}
            type="button"
            onClick={openExpanded}
            aria-label="Open diagram full size"
            className="mx-auto block w-fit cursor-zoom-in rounded focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            // Mermaid's own output, rendered with securityLevel "strict"
            // (sanitized, no scripts or click handlers).
            dangerouslySetInnerHTML={{ __html: effective.svg }}
          />
        </div>
      ) : (
        <div className="flex h-32 items-center justify-center bg-card text-xs text-muted-foreground">
          Drawing diagram…
        </div>
      )}

      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="flex h-[85vh] max-w-[calc(100%-2rem)] flex-col gap-3 sm:max-w-5xl">
          <div className="flex items-center gap-1 pr-8">
            <DialogTitle className="mr-auto text-sm font-medium">Diagram</DialogTitle>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom out"
              disabled={zoom <= ZOOM_MIN}
              onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z - ZOOM_STEP))}
            >
              <Minus className="size-4" />
            </Button>
            <span className="w-12 text-center text-xs tabular-nums text-muted-foreground">
              {Math.round(zoom * 100)}%
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom in"
              disabled={zoom >= ZOOM_MAX}
              onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z + ZOOM_STEP))}
            >
              <Plus className="size-4" />
            </Button>
            <Button variant="ghost" size="icon-sm" aria-label="Reset zoom" onClick={() => setZoom(1)}>
              <RotateCcw className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Download as SVG"
              onClick={handleDownload}
            >
              <Download className="size-4" />
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-border bg-card p-4">
            <div
              ref={dialogRef}
              role="img"
              aria-label="Diagram"
              className="mx-auto w-fit [&>svg]:block"
              dangerouslySetInnerHTML={{
                __html: effective.status === "ready" ? effective.svg : "",
              }}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
