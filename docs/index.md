---
layout: landing
title: TwidgestBot — Twitter в Telegram-канал на автопилоте
description: Multi-tenant Telegram-бот для автоматизации новостных каналов. Адаптирует твиты из X в посты на русском, публикует в твой канал.
---

<style>
.hero { padding: 60px 20px; text-align: center; }
.hero h1 { font-size: 2.4rem; margin-bottom: 0.5em; line-height: 1.2; }
.hero .subtitle { font-size: 1.2rem; color: #555; max-width: 600px; margin: 0 auto 2em; line-height: 1.5; }
.cta-btn {
  display: inline-block;
  background: #229ED9;
  color: white !important;
  padding: 14px 32px;
  border-radius: 8px;
  font-size: 1.1rem;
  font-weight: 600;
  text-decoration: none;
  margin: 8px;
}
.cta-btn:hover { background: #1b8cc4; }
.cta-btn.secondary { background: #555; }

.section { padding: 50px 20px; max-width: 800px; margin: 0 auto; }
.section h2 { font-size: 1.8rem; margin-bottom: 1em; }
.section h3 { font-size: 1.3rem; margin-top: 1.5em; }

/* Скрываем дублирующий sidebar-footer темы Minimal на главной */
.wrapper > section > p:last-of-type a[href*="legal"],
header p:has(a[href*="legal"]) { display: none; }

/* Better mobile padding */
@media (max-width: 600px) {
  .hero { padding: 30px 15px; }
  .section { padding: 30px 15px; }
}

.steps { display: grid; gap: 24px; margin: 30px 0; }
.step {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  padding: 20px;
  background: #f6f8fa;
  border-radius: 8px;
}
.step-num {
  background: #229ED9;
  color: white;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  flex-shrink: 0;
}
.step-text { flex: 1; }
.step-text strong { display: block; margin-bottom: 4px; }

.tiers {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin: 30px 0;
}
.tier {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 20px;
  text-align: center;
}
.tier.featured { border-color: #229ED9; border-width: 2px; }
.tier h4 { margin: 0 0 8px; }
.tier .price { font-size: 1.4rem; font-weight: 600; margin-bottom: 12px; }
.tier ul { list-style: none; padding: 0; text-align: left; font-size: 0.9rem; }
.tier ul li { padding: 4px 0; }

.faq-item { padding: 16px 0; border-bottom: 1px solid #eee; }
.faq-item summary { font-weight: 600; cursor: pointer; padding: 8px 0; }
.faq-item p { margin: 8px 0 0; color: #555; }

footer { text-align: center; padding: 40px 20px; color: #777; font-size: 0.9rem; }
footer a { color: #555; }

@media (max-width: 600px) {
  .hero h1 { font-size: 1.8rem; }
  .hero .subtitle { font-size: 1rem; }
}
</style>

<div class="hero">
<h1>Твой Telegram-канал<br>на автопилоте</h1>
<p class="subtitle">
TwidgestBot собирает твиты из X (Twitter), переводит на русский,
оформляет в посты и публикует в твой канал. Без копипасты, без сидения по 2 часа в день.
</p>
<a class="cta-btn" href="https://t.me/TwidgestBot">Запустить бесплатно →</a>
<a class="cta-btn secondary" href="#how-it-works">Как это работает</a>
</div>

<div class="section" id="how-it-works">
<h2>Как это работает — 3 шага</h2>

<div class="steps">

<div class="step">
<div class="step-num">1</div>
<div class="step-text">
<strong>Выбираешь тему</strong>
15 готовых шаблонов (AI, crypto, longevity, F1, NBA и др.) или описываешь свою — AI подберёт релевантные X-аккаунты и проверит, что они активны.
</div>
</div>

<div class="step">
<div class="step-num">2</div>
<div class="step-text">
<strong>Привязываешь Telegram-канал</strong>
Создаёшь канал, добавляешь @TwidgestBot админом, пересылаешь любое сообщение из канала боту — готово.
</div>
</div>

<div class="step">
<div class="step-num">3</div>
<div class="step-text">
<strong>Получаешь готовые посты</strong>
4 раза в день — структурированный дайджест из лучших твитов. Между ними — отдельные посты с самыми вирусными новостями. С релевантными картинками из Unsplash.
</div>
</div>

</div>

</div>

<div class="section">
<h2>Что внутри</h2>

<h3>Готовые темы (за 30 секунд)</h3>
<p>
🤖 AI & Tech News • 💰 Crypto & Web3 • 🚀 Startups & VC • 🧬 Longevity & Biohacking •
🏎 Formula 1 • 🏀 NBA Basketball • ⚽ World Soccer • 🚀 Space & Astronomy •
🔬 Science & Research • 🎮 Gaming News • 🎨 Design & UX • 🎬 Movies & TV •
📈 Marketing & Growth • 💭 Philosophy & Ideas • ⚡ Tesla & SpaceX
</p>

<h3>Своя тема — AI подбирает источники</h3>
<p>
Описываешь нишу простыми словами ("новости венчурного инвестирования и Y Combinator")
— Sonnet 4.5 генерирует поисковые запросы, Twitter Search находит реальные крупные
аккаунты по теме, AI ранжирует по релевантности. Все источники проверены — мёртвые
аккаунты отсеиваются автоматически.
</p>

<h3>Защита от мусора</h3>
<p>
LLM-фильтр отсекает: ретвиты без своей мысли, рекламу, нытьё, прямые медицинские
советы с дозировками, политически рискованный контент. Дедупликация по теме —
не публикуем 4 поста про одно событие подряд.
</p>

<h3>Управление прямо из Telegram</h3>
<p>
<code>/status</code> — диагностика канала: какие источники работают, что отклонено,
когда следующий дайджест.<br>
<code>/sources</code>, <code>/addsource</code>, <code>/removesource</code> — ручное управление.<br>
<code>/regenerate</code> — пересоздать список источников через AI если первый подбор слабый.<br>
<code>/setimages</code> — переключить картинки on/off для канала.
</p>

</div>

<div class="section">
<h2>Тарифы</h2>

<div class="tiers">

<div class="tier">
<h4>Free</h4>
<div class="price">0 ⭐</div>
<ul>
<li>1 канал</li>
<li>3 источника</li>
<li>До 5 постов в день</li>
<li>Дайджест каждые 6ч</li>
</ul>
</div>

<div class="tier">
<h4>Starter</h4>
<div class="price">99 ⭐ <small>/мес</small></div>
<ul>
<li>2 канала</li>
<li>10 источников</li>
<li>До 20 постов в день</li>
<li>Дайджест каждые 6ч</li>
</ul>
</div>

<div class="tier featured">
<h4>Pro</h4>
<div class="price">299 ⭐ <small>/мес</small></div>
<ul>
<li>5 каналов</li>
<li>30 источников</li>
<li>До 200 постов в день</li>
<li>Claude Sonnet 4.5 для дайджестов</li>
</ul>
</div>

<div class="tier">
<h4>Agency</h4>
<div class="price">999 ⭐ <small>/мес</small></div>
<ul>
<li>20 каналов</li>
<li>100 источников</li>
<li>До 2000 постов в день</li>
<li>Claude Sonnet 4.5</li>
</ul>
</div>

</div>

<p style="text-align:center;color:#666;font-size:0.9rem;">
Платежи через Telegram Stars. 1 ⭐ ≈ 2 рубля. Покупка действует 30 дней,
далее по необходимости — повторная оплата через <code>/upgrade</code>.
</p>

</div>

<div class="section">
<h2>Инструкция: настройка за 5 минут</h2>

<div class="steps">

<div class="step">
<div class="step-num">1</div>
<div class="step-text">
<strong>Открой бота</strong>
Перейди в <a href="https://t.me/TwidgestBot">@TwidgestBot</a>, нажми <strong>Start</strong>.
Получишь приветствие и список команд.
</div>
</div>

<div class="step">
<div class="step-num">2</div>
<div class="step-text">
<strong>Создай канал в боте</strong>
Два способа:<br>
- <code>/templates</code> — выбираешь готовую тему кнопкой (AI, crypto, F1, ...)<br>
- <code>/createchannel ai крикет, премьер-лига Индии</code> — описываешь свою тему,
AI подберёт источники
</div>
</div>

<div class="step">
<div class="step-num">3</div>
<div class="step-text">
<strong>Создай Telegram-канал</strong>
Обычный публичный или приватный канал. Добавь <strong>@TwidgestBot</strong> администратором
с правом «Публикация сообщений».
</div>
</div>

<div class="step">
<div class="step-num">4</div>
<div class="step-text">
<strong>Привяжи канал к боту</strong>
Перешли любое сообщение из своего Telegram-канала боту. Бот автоматически свяжет
его с последним созданным каналом.
</div>
</div>

<div class="step">
<div class="step-num">5</div>
<div class="step-text">
<strong>Готово</strong>
В течение 30 минут начнётся первый цикл сбора. Через 6 часов — первый дайджест
или отдельные виральные посты раньше.<br>
Проверить статус: <code>/status &lt;id_канала&gt;</code>.
</div>
</div>

</div>

<h3>Если что-то не работает</h3>
<p>
<code>/status &lt;id&gt;</code> покажет полную диагностику: сколько твитов в очереди,
какие источники активны, какие отказали и почему. Команда <code>/regenerate &lt;id&gt;</code>
полностью пересоздаст список источников через AI — если первый подбор оказался слабым.
</p>

</div>

<div class="section">
<h2>Частые вопросы</h2>

<details class="faq-item">
<summary>Это законно для русского канала?</summary>
<p>
Да. Бот применяет фильтр контента, который автоматически отсекает материалы,
рискованные по законодательству РФ: военная критика, призывы к санкциям,
психоактивные вещества, прямые медицинские дозировки. Тем не менее, ответственность
за публикуемый контент несёт владелец канала — см.
<a href="legal/terms.html">условия использования</a>.
</p>
</details>

<details class="faq-item">
<summary>Что если AI подобрал плохие источники?</summary>
<p>
Используй <code>/regenerate &lt;id&gt;</code> — бот заменит весь список новыми
источниками через AI. Если и это не подошло — добавь свои руками через
<code>/addsource &lt;id&gt; @username</code>. Бот проверит существование аккаунта в X.
</p>
</details>

<details class="faq-item">
<summary>Сколько стоит подписка в рублях?</summary>
<p>
Telegram Stars — внутренняя валюта Telegram. 1 ⭐ примерно равен 2 рублям
(точный курс зависит от способа покупки звёзд).
Starter (99⭐) ≈ 200₽/мес, Pro (299⭐) ≈ 600₽/мес, Agency (999⭐) ≈ 2000₽/мес.
</p>
</details>

<details class="faq-item">
<summary>Что если канал ничего не постит несколько часов?</summary>
<p>
Открой <code>/status &lt;id&gt;</code>. Там видно, сколько твитов прошло через
фильтр, сколько отклонено, и какие источники молчат. Чаще всего причина —
все источники постят рекламу/ретвиты, которые отсекает фильтр ценности.
В этом случае — <code>/regenerate</code> или ручное управление через
<code>/sources</code>.
</p>
</details>

<details class="faq-item">
<summary>Какие данные собирает бот?</summary>
<p>
Только Telegram user ID, username (если открыт), настройки каналов, историю
платежей. Никаких ФИО, email, телефонов, адресов. Полная информация —
в <a href="legal/privacy.html">политике конфиденциальности</a>.
</p>
</details>

<details class="faq-item">
<summary>Можно ли подключить свой Telegram-канал, который уже работает?</summary>
<p>
Да, любой канал. Добавь @TwidgestBot админом → перешли сообщение из канала
боту → канал привязан. Бот не трогает существующие посты, только добавляет
новые от своего имени.
</p>
</details>

<details class="faq-item">
<summary>Как отменить подписку?</summary>
<p>
Платная подписка — это разовая покупка на 30 дней. По истечении срока тариф
автоматически возвращается на Free. Если не хочешь продолжать — просто не
покупаешь снова. Возврат средств после успешной оплаты не предусмотрен,
кроме случаев технического сбоя.
</p>
</details>

</div>

<div class="section" style="text-align:center;">
<h2>Готов попробовать?</h2>
<p>Бесплатный план без срока — 1 канал, 3 источника, до 5 постов в день.
Достаточно чтобы понять, подходит ли тебе.</p>
<a class="cta-btn" href="https://t.me/TwidgestBot">Запустить бот →</a>
</div>

<footer>
<p>
<a href="legal/privacy.html">Политика конфиденциальности</a> ·
<a href="legal/terms.html">Условия использования</a> ·
<a href="https://github.com/kelbic/twidgest-bot">GitHub</a>
</p>
<p>© 2026 TwidgestBot. Сделано <a href="https://github.com/kelbic">@kelbic</a>.</p>
</footer>
