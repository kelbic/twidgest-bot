---
layout: landing
title: TwidgestBot — Twitter и VK в Telegram-канал на автопилоте
description: Multi-tenant Telegram-бот для автоматизации новостных каналов. Адаптирует твиты из X и посты из VK в посты на русском, публикует в твой канал.
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
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
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
  .hero { padding: 30px 15px; }
  .hero h1 { font-size: 1.8rem; }
  .hero .subtitle { font-size: 1rem; }
  .section { padding: 30px 15px; }
}
</style>

<div class="hero">
<h1>Твой Telegram-канал<br>на автопилоте</h1>
<p class="subtitle">
TwidgestBot собирает посты из X (Twitter) и VK, переводит на русский,
оформляет и публикует в твой канал. Без копипасты, без ручного труда.
</p>
<a class="cta-btn" href="https://t.me/TwidgestBot">Попробовать бесплатно →</a>
<a class="cta-btn secondary" href="#how-it-works">Как это работает</a>
</div>

<div class="section" id="how-it-works">
<h2>Как это работает — 3 шага</h2>
<div class="steps">

<div class="step">
<div class="step-num">1</div>
<div class="step-text">
<strong>Выбираешь тему</strong>
Пишешь тему своими словами — AI подберёт и проверит авторов — или берёшь готовый шаблон (AI, космос, гейминг, кино, F1) —
AI подберёт релевантные X-аккаунты и проверит, что они активны.
Или добавляешь VK-сообщества вручную.
</div>
</div>

<div class="step">
<div class="step-num">2</div>
<div class="step-text">
<strong>Привязываешь Telegram-канал</strong>
Создаёшь канал, добавляешь @TwidgestBot админом, пересылаешь любое сообщение
из канала боту — готово.
</div>
</div>

<div class="step">
<div class="step-num">3</div>
<div class="step-text">
<strong>Получаешь готовые посты</strong>
Структурированные дайджесты несколько раз в день + отдельные посты с самыми
вирусными новостями. С релевантными картинками из Unsplash или прямо из VK.
</div>
</div>

</div>
</div>

<div class="section">
<h2>Что внутри</h2>

<h3>Источники: Twitter/X и VK</h3>
<p>
Добавляй источники из обоих платформ в один канал:<br>
<code>/addsource 5 @elonmusk</code> — Twitter-аккаунт<br>
<code>/addsource 5 vk:lentaru</code> — VK-сообщество<br>
Бот проверяет существование и публичность источника перед добавлением.
</p>

<h3>Готовые темы (за 30 секунд)</h3>
<p>
🤖 AI & Tech • 💰 Crypto & Web3 • 🚀 Startups & VC • 🧬 Longevity & Biohacking •
🏎 Formula 1 • 🏀 NBA • ⚽ Soccer • 🚀 Space & Astronomy •
🔬 Science • 🎮 Gaming • 🎨 Design & UX • 🎬 Movies & TV •
📈 Marketing & Growth • 💭 Philosophy • ⚡ Tesla & SpaceX
</p>

<h3>Три режима фильтрации</h3>
<p>
🎯 <strong>Строгий</strong> — только факты и события с цифрами. Высокая планка.<br>
📡 <strong>Свободный</strong> — новости, реакции, комьюнити-посты. Пропускает почти всё.<br>
⚡ <strong>Без фильтра</strong> — публикует всё кроме юридически рискованного контента.
Переключение: <code>/setfilter &lt;id&gt; strict|loose|unfiltered</code>
</p>

<h3>Управление прямо из Telegram</h3>
<p>
<code>/status &lt;id&gt;</code> — диагностика: источники, очередь, следующий дайджест<br>
<code>/sources</code>, <code>/addsource</code>, <code>/removesource</code> — управление источниками<br>
<code>/setthreshold &lt;id&gt; likes=N retweets=N</code> — настройка порога виральности<br>
<code>/setfilter &lt;id&gt; strict|loose|unfiltered</code> — режим фильтрации<br>
<code>/regenerate &lt;id&gt;</code> — пересоздать источники через AI<br>
<code>/scout &lt;id&gt;</code> — AI-скаут: подобрать новые источники с проверкой<br>
<code>/setimages &lt;id&gt; on|off</code> — картинки в канале<br>
<code>/setlegal &lt;id&gt;</code> — юр-фильтр RF-рисков (вкл/выкл под подтверждение)<br>
<code>/me</code> — каналы и статусы, <code>/upgrade</code> — оплата
</p>

<h3>Недельный отчёт владельцу</h3>
<p>
Каждый понедельник бот присылает сводку по твоим каналам: сколько постов вышло
(одиночных и дайджестов), средний interest-балл потока по оценке AI-редактора,
сколько слабых тем отфильтровано — и сколько часов ручной работы это сэкономило.
</p>

<h3>Защита от мусора</h3>
<p>
LLM-фильтр отсекает рекламу, ретвиты без мысли, политически рискованный контент
(для РФ-аудитории), медицинские дозировки. Дедупликация по теме — не публикуем
4 поста про одно событие подряд. Перед публикацией AI-редактор ранжирует
кандидатов по содержательности: побеждает самый интересный твит, а не просто
самый залайканный, engagement-bait и giveaway отсеиваются.
</p>

<h3>AI-скаут источников</h3>
<p>
Если канал замолчал или источники выдохлись — команда <code>/scout</code>
(или кнопка прямо в алерте о тишине) подберёт новых авторов под тему канала.
Каждого кандидата скаут проверяет по его реальным последним твитам: доля
текстовых постов, медиана лайков, частота постинга, доля твитов по теме
канала (AI-оценка) и сколько постов в неделю автор реально даст именно
твоему каналу. Добавление — только по твоей кнопке, бот сам источники
не меняет.
</p>
</div>

<div class="section" id="essayist">
<h2>✍️ Essayist — авторские разборы <small>(надстройка)</small></h2>
<p>
Essayist — отдельный бот <a href="https://t.me/essayist_bot">@essayist_bot</a>, который работает
<strong>поверх твоих каналов TwidgestBot</strong>. Он берёт виральный твит (или твою тему),
пишет по нему заземлённый авторский разбор на русском — с веб-ресёрчем и проверкой фактов —
и публикует в канал <strong>только после твоего одобрения</strong>. Автопостинга нет.
</p>

<h3>Чем отличается от обычного поста</h3>
<p>
TwidgestBot адаптирует и постит новость. Essayist пишет <strong>разбор</strong>: что стоит за
новостью, конкретный кейс, цифры с источниками. Пайплайн — план → веб-поиск → синтез фактов →
черновик → критик-фактчекер. Если веб-поиск не дал ни одного результата, разбор не выпускается —
защита от выдуманных фактов.
</p>

<h3>Решение всегда за тобой (human-in-the-loop)</h3>
<p>
Бот присылает карточку с готовым разбором и кнопками:<br>
✅ <strong>Опубликовать</strong> — пост уходит в канал<br>
✍️ <strong>Сменить угол</strong> — перегенерировать под другим углом<br>
❌ <strong>Отклонить</strong> — с причиной. Без твоего нажатия ничего не публикуется.
</p>

<h3>Автоподбор тем</h3>
<p>
Можно включить автоподбор по каналу: бот проверяет очередь каждые 30 минут и по выбранной
частоте канала (3 / 6 / 9 / 12 / 24 ч) присылает свежую (не старше суток) топовую тему на
одобрение. Непоисковые темы (мемы без фактуры) бот пропускает сам. По умолчанию автоподбор выключен.
</p>

<h3>Команды</h3>
<p>
<code>/essay &lt;id&gt;</code> — топ свежих тем канала, выбрать кнопкой<br>
<code>/essay &lt;id&gt; all</code> — показать всё, включая уже опубликованное<br>
<code>/essay &lt;id&gt; &lt;своя тема или ссылка&gt;</code> — разбор по своему тексту<br>
<code>/timer</code> — автоподбор: вкл/выкл и частота по каналам<br>
<code>/help</code> — справка по командам
</p>
</div>

<div class="section">
<h2>Как подключить Essayist</h2>
<div class="steps">

<div class="step">
<div class="step-num">1</div>
<div class="step-text">
<strong>Нужен канал в TwidgestBot</strong>
Essayist берёт темы из тех же источников, что собирает TwidgestBot.
Сначала заведи и привяжи канал в <a href="https://t.me/TwidgestBot">@TwidgestBot</a> (см. инструкцию выше).
</div>
</div>

<div class="step">
<div class="step-num">2</div>
<div class="step-text">
<strong>Открой Essayist</strong>
Перейди в <a href="https://t.me/essayist_bot">@essayist_bot</a>, нажми <code>/start</code>.
</div>
</div>

<div class="step">
<div class="step-num">3</div>
<div class="step-text">
<strong>Триал включится сам</strong>
На первом <code>/start</code> бот выдаёт триал: <strong>7 дней и 20 разборов</strong>,
без карты. Дальше — 1490⭐ за 30 дней (20 разборов), оплата кнопкой прямо в боте.
</div>
</div>

<div class="step">
<div class="step-num">4</div>
<div class="step-text">
<strong>Добавь бота в канал</strong>
Добавь @essayist_bot администратором канала с правом «Публикация сообщений» —
без этого он не сможет опубликовать разбор.
</div>
</div>

<div class="step">
<div class="step-num">5</div>
<div class="step-text">
<strong>Поехали</strong>
<code>/essay &lt;id_канала&gt;</code> — выбери тему и получи карточку с разбором.
Или <code>/timer</code> — включи автоподбор по каналу.
</div>
</div>

</div>
</div>

<div class="section">
<h2>Тарифы</h2>
<div class="tiers">

<div class="tier">
<h4>🎁 Триал</h4>
<div class="price">0 ⭐</div>
<ul>
<li>✓ 7 дней бесплатно на первый канал</li>
<li>✓ Полный функционал: AI-подбор и проверка источников, фильтры, дайджесты</li>
<li>✓ До 20 постов в день</li>
<li>✓ Без карты — просто напиши тему боту</li>
</ul>
</div>

<div class="tier featured">
<h4>Канал</h4>
<div class="price">999 ⭐ <small>/ 30 дней</small></div>
<ul>
<li>✓ Цена за канал — платишь только за то, что работает</li>
<li>✓ До 15 источников на канал (X + VK)</li>
<li>✓ До 50 постов в день</li>
<li>✓ AI-редактор, ранжирование, все режимы фильтрации</li>
<li>✓ AI-скаут источников</li>
</ul>
</div>

</div>
<p style="text-align:center;color:#666;font-size:0.9rem;">
Платежи через Telegram Stars (999⭐ ≈ 1000-1500₽ в зависимости от способа
покупки). Оплата добавляет 30 дней к текущей дате окончания канала.
Без оплаты канал просто молчит — настройки и история сохраняются.
</p>
</div>

<div class="section">
<h2>Инструкция: настройка за 5 минут</h2>
<div class="steps">

<div class="step">
<div class="step-num">1</div>
<div class="step-text">
<strong>Открой бота</strong>
Перейди в <a href="https://t.me/TwidgestBot">@TwidgestBot</a>, нажми Start.
</div>
</div>

<div class="step">
<div class="step-num">2</div>
<div class="step-text">
<strong>Создай канал в боте</strong>
Просто напиши боту тему одним сообщением («крикет, IPL») — AI подберёт
и проверит реальные источники из X.<br>
Или <code>/templates</code> — готовая тема одной кнопкой.
</div>
</div>

<div class="step">
<div class="step-num">3</div>
<div class="step-text">
<strong>Создай Telegram-канал</strong>
Добавь @TwidgestBot администратором с правом «Публикация сообщений».
</div>
</div>

<div class="step">
<div class="step-num">4</div>
<div class="step-text">
<strong>Привяжи канал к боту</strong>
Перешли любое сообщение из Telegram-канала боту. Готово.
</div>
</div>

<div class="step">
<div class="step-num">5</div>
<div class="step-text">
<strong>Готово</strong>
В течение 30 минут начнётся первый цикл сбора.
Проверить статус: <code>/status &lt;id_канала&gt;</code>
</div>
</div>

</div>
</div>

<div class="section">
<h2>Частые вопросы</h2>

<details class="faq-item">
<summary>Это законно для русского канала?</summary>
<p>
Да. Бот применяет двухслойный фильтр контента. Слой юридических рисков РФ
(военная критика, призывы к санкциям) включён по умолчанию; владелец канала может
осознанно отключить его командой <code>/setlegal</code> — с явным подтверждением,
факт отключения фиксируется. Фильтр психоактивных веществ и медицинских дозировок
не отключается. Ответственность за публикуемый контент несёт владелец канала — см.
<a href="legal/terms.html">условия использования</a>.
</p>
</details>

<details class="faq-item">
<summary>Можно добавлять VK-источники?</summary>
<p>
Да. Используй команду <code>/addsource &lt;id&gt; vk:domain</code>, например
<code>/addsource 5 vk:lentaru</code>. Бот проверит что сообщество публичное и существует.
Twitter и VK источники работают в одном канале одновременно.
</p>
</details>

<details class="faq-item">
<summary>Что если AI подобрал плохие источники?</summary>
<p>
Используй <code>/regenerate &lt;id&gt;</code> — бот заменит весь список.
Или добавь свои через <code>/addsource &lt;id&gt; @username</code> или <code>vk:domain</code>.
</p>
</details>

<details class="faq-item">
<summary>Сколько стоит подписка в рублях?</summary>
<p>
Telegram Stars — внутренняя валюта Telegram. 1 ⭐ примерно равен 1-2 рублям
(точный курс зависит от способа покупки). Канал (999⭐) ≈ 1000-1500₽ за 30 дней.
</p>
</details>

<details class="faq-item">
<summary>Что если канал ничего не постит несколько часов?</summary>
<p>
Открой <code>/status &lt;id&gt;</code>. Там видно сколько твитов в очереди,
какие источники активны и что отклонено. Команда <code>/regenerate &lt;id&gt;</code>
пересоздаст список источников через AI.
</p>
</details>

<details class="faq-item">
<summary>Какие данные собирает бот?</summary>
<p>
Только Telegram user ID, username, настройки каналов, историю платежей.
Никаких ФИО, email, телефонов. Полная информация —
<a href="legal/privacy.html">политика конфиденциальности</a>.
</p>
</details>

<details class="faq-item">
<summary>Как отменить подписку?</summary>
<p>
Оплата канала — разовая покупка на 30 дней, не автосписание. По истечении
канал просто перестаёт публиковать; источники и настройки сохраняются.
Просто не покупаешь снова. Возврат средств после успешной оплаты не предусмотрен
кроме технических сбоев.
</p>
</details>

<details class="faq-item">
<summary>Чем Essayist отличается от обычных постов?</summary>
<p>
TwidgestBot адаптирует и постит новости. Essayist пишет авторский разбор по одной теме —
с веб-ресёрчем, источниками и проверкой фактов — и публикует только после твоего одобрения.
Это отдельный бот <a href="https://t.me/essayist_bot">@essayist_bot</a>, надстройка над TwidgestBot.
</p>
</details>

<details class="faq-item">
<summary>Essayist постит сам, без меня?</summary>
<p>
Нет. Бот присылает готовый разбор карточкой с кнопками — публикуется только когда ты нажмёшь
«Опубликовать». Можно перегенерировать под другим углом или отклонить. Автоподбор по таймеру
тоже шлёт карточку на одобрение, а не постит напрямую.
</p>
</details>

<details class="faq-item">
<summary>Нужен ли отдельный канал для Essayist?</summary>
<p>
Нет. Essayist работает на твоих существующих каналах TwidgestBot и постит в них же.
Нужно только добавить @essayist_bot админом канала с правом публикации.
</p>
</details>

</div>

<div class="section" style="text-align:center;">
<h2>Готов попробовать?</h2>
<p>14 дней бесплатно — 3 канала, 10 источников (Twitter + VK), до 50 постов в день.</p>
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
