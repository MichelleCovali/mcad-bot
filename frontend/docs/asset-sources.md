# Источники оформления

Исходный визуальный образец: [maritimecybersecurity.nl](https://maritimecybersecurity.nl/). Дата изучения и сохранения публичных элементов оформления: **2026-10-21**.

Локальная копия воспроизводит основной экран карты: шапку, логотип, поиск, фильтры, меню, кнопки масштаба и выбора слоя. Структура и основные размеры, цвета и отступы сверены с публичными файлами [main.css](https://maritimecybersecurity.nl/css/main.css) и [map.css](https://maritimecybersecurity.nl/css/map.css). Реализация интерфейса написана локально на React.

В `public/assets/img/` сохранены оригинальные публичные элементы оформления:

- `logo.png`, `search.png`, `Funnel.png`;
- `layer_top.svg`, `layer_bottom.svg`, `plus.svg`, `minus.svg`;
- `icons/Map_Icon.png`, `icons/Map_Icon_Selected.png`;
- `icons/List_icon.png`, `icons/List_icon_selected.png`;
- `icons/info_icon.png`, `icons/info_icon_Selected.png`;
- `icons/Plus_icon.png`, `icons/Plus_icon_selected.png`;
- `markers/Boat.png` — оформление будущего маркера, без записей инцидентов;
- `favicons/apple-touch-icon.png`, `favicons/favicon-16x16.png`, `favicons/favicon-32x32.png`.

Исходные адреса изображений имеют вид `https://maritimecybersecurity.nl/img/<путь>`. Локальные шрифты Cera Round Pro (400, 500, 700) в `public/assets/fonts/` получены из публичной [таблицы шрифта CDNFonts](https://fonts.cdnfonts.com/css/cera-round-pro), которую подключает исходный сайт. Обозначения и логотипы принадлежат соответствующим правообладателям.

Данные оригинального backend, база инцидентов, исходные ключи API и аналитика не копировались. По умолчанию записей и демонстрационных маркеров нет. Данные поступают только из отдельно настроенного `VITE_API_BASE_URL`.

Google Maps подключается только с собственным ключом разработчика. Без него используется OpenStreetMap для дорожного слоя и Esri World Imagery для спутникового; подписи, картографическое оформление и детализация отличаются от Google Maps на образце. Атрибуция поставщиков отображается на карте.

Пункты **List view**, **Info** и **Report** открывают локальные панели этого прототипа. Это ограниченные панели для разработки, а не точные копии соответствующих страниц исходного сайта. Основная область копирования — экран карты; отправка отчётов пока не подключена.
