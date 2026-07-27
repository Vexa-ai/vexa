"use client";
/** The brand mark. One component so a rebrand swaps the image in one place — it was inlined as a
 *  raw <img> in the workbench rail and on the sign-in screen, which is two places to miss. */
import { BRAND } from "./brand";

export function BrandLogo({ size = 24, radius = 7 }: { size?: number; radius?: number }) {
  return (
    <img
      src={BRAND.logo}
      alt={BRAND.name}
      width={size}
      height={size}
      style={{ borderRadius: radius, display: "block", flex: "none" }}
    />
  );
}
