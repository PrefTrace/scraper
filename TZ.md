ВАЖНО!!! ЭТО НЕ ОБЬЕДЕНЕННЫЕ ДАННЫЕ - лишь первчиный маппинг по тому (мы не дублируем таблицы, а перерабатываем так, как удобно хранить, но по смыслу очень близко), как их понимает первоисточник, мердж источников - отдельный этап

# Steam:

## общая инфа

appid
тип - игра/приложение/dlc/soundtrack

demoid (nullable) (сами демки нет смысла парсить)
dlcforappid (nullable) (для dlc обязательно)
optionaldlc (nullable / bool)
requiredappid (nullable) (какой appid нужно владеть чтобы была возможность купить этот (для dlc что логично - заполняем))

билд для линукса?
билд для винды?
билд для мака?

vac включен?

название игры на метакритике
рейтинг критиков на меткритике
ссылка на метакритик

лучше играть на геймпаде? (Gamepad Preferred)
все части игры поддерживают геймпад? - none/partial/full (Full Controller Support / "controller_support")

дата выхода (в том числе если еще не вышла - в будущем) (для диапазона - минимально допустимое время и дата)
крайняя дата выхода (для еще не вышедших конвертируем стимовское "q3", "в сентябре" и тп в нормальные диапазоны)
состояние: не вышла, вышла для предзаказавших, в раннем доступе, вышла, снята с продаж

уведомление о внешнем аккаунте (текст исключительно на англ)
уведомление о внешнем drm/античите (текст исключительно на англ)

## медиа

appid
type (скриншот, трейлер, Header Capsule, Small Capsule, Main Capsule, Vertical Capsule, Page Background, Library Capsule, Library Header, Library Hero, Library Logo)
url
format (тип файла) (индексируем премущественно webm для видео)
язык BCP 47

## приложение <-> издание

appid
packedgeid

## инфа о изданиях

packedgeid
название издания (на англ исключительно - уже нормализированный от цены)
описание издания

## цены изданий

packedgeid

валюта (steam price region)

* если цены null - не продается
int цена (0 == постоянно бесплатно)
int конечная цена
% скидки

тип (единоразово/регулярная)
период (час/день/неделя/месяц/год)
периодо-единиц (int) в подписке

## наборы (бандлы)

bundleid
name
стандартная скидка за бандл %
must_purchase_as_set

## набор <-> издание

packadgeid
bundleid

## цены наборов

bundleid
валюта (steam price region)
эффективная скидка
базовая цена (int)
финальная цена (int)

## внешние ссылки

appid
тип (название соц.сети на англ в нижнем регистре без спец. символов + support_website и support_email (сюда же записываем))
url
value (можно внести вместо url)

## возврастные ограничения (на английской локали, оно все равно покажет все)

appid
название стандарта (в том числе из поля content_descriptors представляем как steam)
ageid (appid+стандарт)

рейтинг оценен самим разрабом? ("rating_generated") (nullable / bool)
показывать окно-предупреждение? ("use_age_gate") (nullable / bool)
запрещен для продажи по данному рейтингу? (nullable / bool)

рейтинг (текстовый дескриптор) (nullable)
минимальный возвраст (nullable)
дескриптор (raw) (nullable)

* для nullable имеется ввиду в случаях content_descriptors и редких таких как bbfc который дает только поле "рейтинг"

## дескрипторы (парсим raw)

ageid
steamid (для content_descriptors источника / nullable)
name (display_online_notice как отдельное поле не сохраняем а превращаем в дескриптор с заранее определенным английским текстом)

## системные требования

appid
платформа
уровень (минимальные / рекомендованные)
html (на этом этапе парсинг содержимого не предполагается) ВАЖНО - используем АНГЛИЙСКИЙ текст (полученный с англ страницы)

## "фичи" игры

appid
id категории (smallint)
english name

## функции доступности

appid
id категории (smallint)
english name

## поддержка стимдек

appid
steamdeck статус - неизвестно / неподдерживается / играбельно / поддерживается
TODO - доп поля детализации

## eula сторонние

appid
id
имя-описание (вроде бы единое поле? TODO)
steam-link support (являеться отдельным полем? TODO)
url
version

## контроллеры

контроллер (xbox, дуалшок, дуалсенс и тп на англ, пробелы и регистры допустимы)
поддержка bt?
поддержка usb?

## разработчики и издатели

appid
статус (разраб/издатель)
id организации

## общая инфа-языки

appid
язык BCP 47

название
короткое описание
об игре
длинное описание
legal-notice

## поддерживаемые языки

appid
язык BCP 47

озвучка?
текст?
субтитры?

## билд-ветки

название ветки
время обновления
описание ветки
buildid
размер для скачки
размер на диске

## отзывы-языки статистика

appid
язык BCP 47 (допустимо nullable при записи ALL)
кол-во отзывов
кол-во отрицательных
кол-во положительных
review-score (1-9)

## отзывы по игре

appid
userid

playtime_forever
playtime_last_two_weeks
playtime_at_review
deck_playtime_at_review

datetime_last_played
datetime_created
datetime_updated
datetime_dev_responded

votes_up
votes_funny
weighted_vote_score
comment_count

steam_purchase
received_for_free
written_during_early_access
primarily_steam_deck

voted_up (рекомендует ли игру)
language (BCP 47)
review_text
developer_response

## внешние отзывы указанные разрабом (только английские)

appid
организация
оценка (raw, обычно это `x/y`, бывают без оценки)
ссылка
цитата

## ачивки

appid
id
урл картинки
% полученных у игроков
является скрытой?

### ачивки-языки

id ачивки
язык BCP 47

название
описание

### CCU (В ПЕРВОЙ ВЕРСИИ НЕ РЕАЛИЗОВЫВАТЬ!) - нужен собственный сборщик

N минут считывать текущий CCU

дальше выводим

период - вчера/последние 7 полных суток/последние 30 полных суток/все время
min
max
avg
median

# GamesVoice



# Другие источники которые планируются:

GamesVoice
Mechanics VoiceOver
GOG
EGS
XBox
VKPlay
PCGamingWiki
IGDB
WikiData
areweanticheatyet.com / https://github.com/AreWeAntiCheatYet/AreWeAntiCheatYet/blob/master/games.json
VNDB.org
HLTB
vaporlens.app
