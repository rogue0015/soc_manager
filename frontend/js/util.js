// Small shared helpers: DOM building, dates, text length, toasts, storage.

/** Create an element. `h('a', {href: '#', class: 'x'}, 'text', childEl)`. Text is never parsed as HTML. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') {
      for (const [prop, val] of Object.entries(v)) {
        if (prop.startsWith('--')) el.style.setProperty(prop, val); // custom properties need setProperty
        else el.style[prop] = val;
      }
    }
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c);
  return el;
}

// ---- dates (all local time; the API converts to/from UTC) -----------------

const pad = (n) => String(n).padStart(2, '0');
/** Local YYYY-MM-DD key for a Date. */
export const dateKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const timeValue = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
/** Combine <input type=date> and <input type=time> values into a Date in local time. */
export const fromInputs = (date, time) => new Date(`${date}T${time}`);
export const startOfMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
export const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes());
export const sameDay = (a, b) => dateKey(a) === dateKey(b);

export const fmt = {
  month: (d) => d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }),
  day: (d) => d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }),
  shortDate: (d) => d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }),
  time: (d) => d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
  dayMonth: (d) => d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }),
  dateTime: (d) => `${fmt.shortDate(d)}, ${fmt.time(d)}`,
  /** "just now", "5 min ago", "in 2 h", "3 days ago". */
  ago(d, now = new Date()) {
    const s = Math.round((now - d) / 1000);
    const a = Math.abs(s);
    if (a < 45) return 'just now';
    const [n, unit] = a < 3600 ? [Math.round(a / 60), 'min'] : a < 86400 ? [Math.round(a / 3600), 'h']
      : [Math.round(a / 86400), Math.round(a / 86400) === 1 ? 'day' : 'days'];
    return s >= 0 ? `${n} ${unit} ago` : `in ${n} ${unit}`;
  },
  /** Uptime-style length: "45 s", "12 min", "3 h 5 min", "2 days 4 h". */
  duration(seconds) {
    const m = Math.floor(seconds / 60), hrs = Math.floor(m / 60), days = Math.floor(hrs / 24);
    if (seconds < 60) return `${seconds} s`;
    if (m < 60) return `${m} min`;
    if (hrs < 24) return `${hrs} h ${m % 60} min`;
    return `${days} ${days === 1 ? 'day' : 'days'} ${hrs % 24} h`;
  },
  bytes: (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`),
  num: (n) => n.toLocaleString(),
  /** "1 post", "3 posts". */
  count: (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`,
  /** "Wed, 28 Oct, 12:54" without the year, for lists. */
  when: (d) => `${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}, ${fmt.time(d)}`,
  percent: (r) => (r == null ? '—' : `${Math.round(r * 100)}%`),
};

/** A sensible default time for a new post on `day`: 09:00, or the next full hour if that has passed. */
export function defaultTimeFor(day) {
  const now = new Date();
  if (!sameDay(day, now) || now.getHours() < 8) return '09:00';
  if (now.getHours() >= 23) return '23:59';
  return `${pad(now.getHours() + 1)}:00`;
}

// ---- text length (mirror of PlatformInfo.text_length in the backend) ------

const URL_RE = /https?:\/\/\S+/g;
export function textLength(platform, text) {
  if (platform.url_weight == null) return text.length;
  return text.replace(URL_RE, 'x'.repeat(platform.url_weight)).length;
}

/** Share text for a blog post that fits every chosen platform (mirror of blog.compose_text). */
export function composeBlogText(post, platforms) {
  const fits = (t) => platforms.every((p) => textLength(p, t) <= p.char_limit);
  const { title, description: desc, url } = post;
  const full = desc ? `${title}\n\n${desc}\n\n${url}` : `${title}\n\n${url}`;
  if (fits(full)) return full;
  const words = desc.split(/\s+/);
  while (words.length) {
    words.pop();
    const t = `${title}\n\n${words.join(' ').replace(/[.,;:]+$/, '')}…\n\n${url}`;
    if (fits(t)) return t;
  }
  return `${title}\n\n${url}`;
}

/** Tell every view that posts changed (saved, deleted, edited or deleted on a platform). */
export const announcePostsChanged = () => window.dispatchEvent(new Event('sm:posts-changed'));

// ---- toast ---------------------------------------------------------------

let toastTimer;
export function toast(message, { error = false } = {}) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.toggle('bad', error);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), error ? 6000 : 3000);
}

// ---- per-browser preferences (safe if storage is blocked) ----------------

export const prefs = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(`sm.${key}`)) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`sm.${key}`, JSON.stringify(value)); } catch { /* ignore */ }
  },
};
