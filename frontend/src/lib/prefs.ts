export type Theme = 'dark' | 'light'
export type Lang = 'ru' | 'en'

const THEME_KEY = 'adp-theme'
const LANG_KEY = 'adp-lang'

export function loadTheme(): Theme {
  const q = new URLSearchParams(window.location.search).get('theme')
  if (q === 'light' || q === 'dark') return q
  return (localStorage.getItem(THEME_KEY) as Theme) || 'dark'
}

export function saveTheme(t: Theme) {
  localStorage.setItem(THEME_KEY, t)
  document.documentElement.dataset.theme = t
}

export function loadLang(): Lang {
  const q = new URLSearchParams(window.location.search).get('lang')
  if (q === 'en' || q === 'ru') return q
  return (localStorage.getItem(LANG_KEY) as Lang) || 'ru'
}

export function saveLang(l: Lang) {
  localStorage.setItem(LANG_KEY, l)
}
