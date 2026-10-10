import "./app-shell.css";
import {AppShell} from "./app-shell";

/** Every page of the main app shares the sidebar, search and light theme. */
export default function AppLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <AppShell>{children}</AppShell>;
}
