const CONTACTS_URL = 'https://lyakoway.vercel.app/contacts'

/** Detects provider-side failures worth explaining in a friendly modal:
 *  exhausted balance (429), quota, invalid/missing API key (401/403). */
export function isProviderError(text: string | undefined | null): boolean {
  if (!text) return false
  return /insufficient balance|please recharge|error code:\s*429|status code:\s*429|баланс|пополн|quota|api key|unauthorized|error code:\s*401|error code:\s*403/i.test(
    text,
  )
}

export function ProviderErrorModal({
  message,
  lang,
  onClose,
}: {
  message: string
  lang: 'ru' | 'en'
  onClose: () => void
}) {
  const t = {
    title: lang === 'en' ? '⚡ Model unavailable' : '⚡ Модель недоступна',
    body:
      lang === 'en'
        ? 'The selected LLM provider declined the request — most often this means the account balance is exhausted or the API key is invalid. Top up the provider balance, choose another model in the selector, or contact us and we will help you set it up.'
        : 'Выбранный LLM-провайдер отклонил запрос — чаще всего это значит, что исчерпан баланс аккаунта или неверный API-ключ. Пополните баланс провайдера, выберите другую модель в селекторе — или напишите нам, поможем настроить.',
    contact: lang === 'en' ? 'Contact us' : 'Связаться с нами',
    close: lang === 'en' ? 'Close' : 'Закрыть',
    whatHappened: lang === 'en' ? 'Details' : 'Подробности',
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">{t.title}</h3>
        <p className="modal-desc">{t.body}</p>
        <details className="sql-block pg-error-detail">
          <summary>{t.whatHappened}</summary>
          <pre>
            <code>{message.slice(0, 500)}</code>
          </pre>
        </details>
        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            {t.close}
          </button>
          <a
            className="btn btn-primary"
            href={CONTACTS_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            ✉ {t.contact}
          </a>
        </div>
      </div>
    </div>
  )
}
