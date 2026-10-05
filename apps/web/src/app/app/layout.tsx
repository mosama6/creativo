"use client";

import { StudioFrame } from "@/components/studio-frame";

export default function StudioLayout({ children }: { children: React.ReactNode }) {
  return <StudioFrame>{children}</StudioFrame>;
}
