import { ImageResponse } from "next/og";

export const size = { width: 32, height: 32 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#D3FB52",
          borderRadius: 8,
          overflow: "hidden",
        }}
      >
        <svg width="20" height="20" viewBox="0 0 40 40">
          <rect x="6" y="7" width="28" height="6" rx="3" fill="#052326" />
          <rect x="6" y="17" width="20" height="6" rx="3" fill="#052326" />
          <rect x="6" y="27" width="12" height="6" rx="3" fill="#052326" />
        </svg>
      </div>
    ),
    { ...size }
  );
}
