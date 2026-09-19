import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SignalLens",
  description: "Evidence-led equity research dashboard",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

