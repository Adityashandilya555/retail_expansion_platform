// DEMO ONLY — deliberately bad TypeScript to show what SonarQube Cloud catches on a PR. Never merge.

// Security hotspot: hard-coded IP address
const API_HOST = "192.168.1.20";

export function sessionId(): string {
  // Security hotspot: Math.random() is not cryptographically secure — use crypto.randomUUID()
  return Math.random().toString(36).slice(2);
}

export function render(el: HTMLElement, userText: string): void {
  // Security hotspot / XSS risk: writing user input as HTML
  el.innerHTML = userText;
}

export function runRule(rule: string): unknown {
  // Vulnerability / hotspot: dynamic code execution
  return eval(rule);
}

export function isAdmin(role: any): boolean {
  // Code smell: loose equality and `any`
  if (role == "admin") {
    return true;
  }
  return false;
}

export const host = API_HOST;
