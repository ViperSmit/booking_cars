const appHeaderDay = document.getElementById("app-header-day");
const appHeaderDate = document.getElementById("app-header-date");
const appHeaderTime = document.getElementById("app-header-time");
const appHeaderWeatherIcon = document.getElementById("app-header-weather-icon");
const appHeaderWeatherTemp = document.getElementById("app-header-weather-temp");
const appHeaderWeatherStatus = document.getElementById("app-header-weather-status");

const APP_HEADER_WEATHER_URL = "https://api.open-meteo.com/v1/forecast?latitude=41.6168&longitude=41.6367&current=temperature_2m,weather_code&timezone=auto";
const APP_HEADER_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const APP_HEADER_WEEKDAYS = ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"];

function updateAppHeaderClock() {
    if (!appHeaderDay || !appHeaderDate || !appHeaderTime) {
        return;
    }

    const now = new Date();
    appHeaderDay.textContent = APP_HEADER_WEEKDAYS[now.getDay()];
    appHeaderDate.textContent = `${String(now.getDate()).padStart(2, "0")} ${APP_HEADER_MONTHS[now.getMonth()]}`;
    appHeaderTime.textContent = now.toLocaleTimeString("ru-RU", {hour12: false});
}

function mapAppHeaderWeatherCode(weatherCode) {
    const code = Number(weatherCode);
    if (code === 0) {
        return {icon: "☀️", label: "Ясно"};
    }
    if ([1, 2].includes(code)) {
        return {icon: "⛅", label: "Переменная облачность"};
    }
    if (code === 3) {
        return {icon: "☁️", label: "Пасмурно"};
    }
    if ([45, 48].includes(code)) {
        return {icon: "🌫️", label: "Туман"};
    }
    if ([51, 53, 55, 56, 57].includes(code)) {
        return {icon: "🌦️", label: "Морось"};
    }
    if ([61, 63, 65, 66, 67, 80, 81, 82].includes(code)) {
        return {icon: "🌧️", label: "Дождь"};
    }
    if ([71, 73, 75, 77, 85, 86].includes(code)) {
        return {icon: "🌨️", label: "Снег"};
    }
    if ([95, 96, 99].includes(code)) {
        return {icon: "⛈️", label: "Гроза"};
    }
    return {icon: "☁️", label: "Неизвестно"};
}

async function loadAppHeaderWeather() {
    if (!appHeaderWeatherIcon || !appHeaderWeatherTemp || !appHeaderWeatherStatus) {
        return;
    }

    try {
        const response = await fetch(APP_HEADER_WEATHER_URL);
        if (!response.ok) {
            throw new Error("weather_request_failed");
        }

        const payload = await response.json();
        const current = payload.current || {};
        const weather = mapAppHeaderWeatherCode(current.weather_code);
        const temperature = current.temperature_2m;

        appHeaderWeatherIcon.textContent = weather.icon;
        appHeaderWeatherTemp.textContent = temperature === undefined ? "--°C" : `${Math.round(Number(temperature))}°C`;
        appHeaderWeatherStatus.textContent = weather.label;
    } catch (error) {
        appHeaderWeatherIcon.textContent = "☁️";
        appHeaderWeatherTemp.textContent = "--°C";
        appHeaderWeatherStatus.textContent = "Погода недоступна";
    }
}

updateAppHeaderClock();
setInterval(updateAppHeaderClock, 1000);
loadAppHeaderWeather();
setInterval(loadAppHeaderWeather, 15 * 60 * 1000);
