(function () {
    const chartRegistry = new Map();
    function $(selector, root = document) {
        return root.querySelector(selector);
    }

    function $all(selector, root = document) {
        return Array.from(root.querySelectorAll(selector));
    }

    function formatMoney(value) {
        const number = Number(value || 0);
        return `$${number.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    }

    function formatNumber(value) {
        return Number(value || 0).toLocaleString();
    }

    function formatPercent(value) {
        return `${Number(value || 0).toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 2})}%`;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function buildQuery(params) {
        const search = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== "") {
                search.set(key, String(value));
            }
        });
        return search.toString();
    }

    async function fetchJson(url) {
        const response = await fetch(url, {headers: {"Accept": "application/json"}});
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.error || "Failed to load analytics");
        }
        return payload;
    }

    function syncUrl(basePath, query) {
        const queryString = buildQuery(query);
        const nextUrl = queryString ? `${basePath}?${queryString}` : basePath;
        window.history.replaceState({}, "", nextUrl);
    }

    function setCardValue(root, key, value, format) {
        const node = $(`[data-summary-key="${key}"]`, root);
        if (!node) {
            return;
        }
        if (format === "money") {
            node.textContent = formatMoney(value);
        } else if (format === "percent") {
            node.textContent = formatPercent(value);
        } else {
            node.textContent = formatNumber(value);
        }
    }

    function destroyChart(id) {
        const chart = chartRegistry.get(id);
        if (chart) {
            chart.destroy();
            chartRegistry.delete(id);
        }
    }

    function renderChart(canvasId, payload, type, label, options = {}) {
        const shell = document.getElementById(`${canvasId}-shell`);
        const canvas = document.getElementById(canvasId);
        if (!shell || !canvas) {
            return;
        }

        destroyChart(canvasId);
        shell.innerHTML = "";
        shell.appendChild(canvas);

        if (!window.Chart) {
            shell.innerHTML = `<div class="analytics-empty">Chart.js недоступен. График не может быть построен.</div>`;
            return;
        }

        if (!payload || payload.is_empty || !payload.labels?.length) {
            shell.innerHTML = `<div class="analytics-empty">${escapeHtml(payload?.empty_message || "Нет данных")}</div>`;
            return;
        }

        const chart = new Chart(canvas, {
            type,
            data: {
                labels: payload.labels,
                datasets: [
                    {
                        label,
                        data: payload.data,
                        borderColor: options.borderColor || "#2857a4",
                        backgroundColor: options.backgroundColor || "rgba(40, 87, 164, 0.18)",
                        borderWidth: 2,
                        tension: 0.34,
                        fill: options.fill ?? (type === "line"),
                    },
                ],
            },
            options: {
                maintainAspectRatio: false,
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: options.moneyAxis ? value => formatMoney(value) : undefined,
                        },
                    },
                },
                plugins: {
                    legend: {
                        display: Boolean(options.showLegend),
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const value = context.parsed.y ?? context.parsed;
                                if (options.moneyAxis) {
                                    return `${context.dataset.label}: ${formatMoney(value)}`;
                                }
                                if (options.percentAxis) {
                                    return `${context.dataset.label}: ${formatPercent(value)}`;
                                }
                                return `${context.dataset.label}: ${formatNumber(value)}`;
                            },
                        },
                    },
                },
            },
        });

        chartRegistry.set(canvasId, chart);
    }

    function renderDonutChart(canvasId, payload, label) {
        const shell = document.getElementById(`${canvasId}-shell`);
        const canvas = document.getElementById(canvasId);
        if (!shell || !canvas) {
            return;
        }

        destroyChart(canvasId);
        shell.innerHTML = "";
        shell.appendChild(canvas);

        if (!window.Chart) {
            shell.innerHTML = `<div class="analytics-empty">Chart.js недоступен. График не может быть построен.</div>`;
            return;
        }

        if (!payload || payload.is_empty || !payload.labels?.length) {
            shell.innerHTML = `<div class="analytics-empty">${escapeHtml(payload?.empty_message || "Нет данных")}</div>`;
            return;
        }

        const chart = new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: payload.labels,
                datasets: [
                    {
                        label,
                        data: payload.data,
                        backgroundColor: ["#2857a4", "#163f87", "#1f9d61", "#d89a00", "#6b7c93", "#cf3f3f"],
                    },
                ],
            },
            options: {
                maintainAspectRatio: false,
                responsive: true,
                plugins: {
                    legend: {
                        position: "bottom",
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                return `${context.label}: ${formatMoney(context.parsed)}`;
                            },
                        },
                    },
                },
            },
        });

        chartRegistry.set(canvasId, chart);
    }

    function downloadBlob(filename, content, mimeType) {
        const blob = new Blob([content], {type: mimeType});
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
    }

    function initFleetPage() {
        const root = document.getElementById("fleet-analytics-root");
        if (!root) {
            return;
        }

        const periodField = $("#filter-period", root);
        const startField = $("#filter-start-date", root);
        const endField = $("#filter-end-date", root);
        const carPicker = $("#filter-car-picker", root);
        const carToggle = $("#filter-car-toggle", root);
        const carMenu = $("#filter-car-menu", root);
        const carSummary = $("#filter-car-summary", root);
        const carCheckboxes = $all('input[name="car_ids"]', root);
        const applyButton = $("#filter-apply", root);
        const resetButton = $("#filter-reset", root);
        const loading = $("#fleet-loading", root);
        const error = $("#fleet-error", root);
        const tableBody = $("#fleet-table-body", root);
        const paginationInfo = $("#fleet-pagination-info", root);
        const prevButton = $("#fleet-prev-page", root);
        const nextButton = $("#fleet-next-page", root);
        const csvButton = $("#fleet-export-csv", root);
        const excelButton = $("#fleet-export-excel", root);

        const tableState = {
            rows: [],
            sortKey: "revenue",
            sortDir: "desc",
            page: 1,
            pageSize: 10,
        };

        function toggleCustomFields() {
            const isCustom = periodField.value === "custom";
            startField.closest(".analytics-filter").classList.toggle("analytics-hidden", !isCustom);
            endField.closest(".analytics-filter").classList.toggle("analytics-hidden", !isCustom);
        }

        function queryState() {
            const selectedIds = carCheckboxes.filter(input => input.checked).map(input => input.value);
            return {
                period: periodField.value,
                start_date: startField.value,
                end_date: endField.value,
                car_ids: selectedIds.join(","),
            };
        }

        function updateCarSummary() {
            const selected = carCheckboxes.filter(input => input.checked);
            if (!selected.length) {
                carSummary.textContent = "Все автомобили";
                return;
            }
            if (selected.length <= 2) {
                carSummary.textContent = selected.map(input => input.dataset.carName || input.value).join(", ");
                return;
            }
            carSummary.textContent = `Выбрано авто: ${selected.length}`;
        }

        function closeCarMenu() {
            carMenu.classList.add("analytics-hidden");
        }

        function toggleCarMenu() {
            carMenu.classList.toggle("analytics-hidden");
        }

        function sortedRows() {
            const rows = [...tableState.rows];
            rows.sort((a, b) => {
                const left = a[tableState.sortKey];
                const right = b[tableState.sortKey];
                const leftNumber = typeof left === "number" ? left : Number(left);
                const rightNumber = typeof right === "number" ? right : Number(right);
                let comparison;
                if (!Number.isNaN(leftNumber) && !Number.isNaN(rightNumber)) {
                    comparison = leftNumber - rightNumber;
                } else {
                    comparison = String(left ?? "").localeCompare(String(right ?? ""));
                }
                return tableState.sortDir === "asc" ? comparison : -comparison;
            });
            return rows;
        }

        function paginatedRows() {
            const rows = sortedRows();
            const startIndex = (tableState.page - 1) * tableState.pageSize;
            return rows.slice(startIndex, startIndex + tableState.pageSize);
        }

        function renderTable() {
            const rows = paginatedRows();
            if (!tableState.rows.length) {
                tableBody.innerHTML = `<tr><td colspan="15"><div class="analytics-empty">Нет данных по автопарку для выбранных фильтров.</div></td></tr>`;
                paginationInfo.textContent = "0 строк";
                prevButton.disabled = true;
                nextButton.disabled = true;
                return;
            }

            tableBody.innerHTML = rows.map(row => `
                <tr data-car-id="${row.id}">
                    <td><div class="analytics-photo-pill">${escapeHtml(row.photo_label)}</div></td>
                    <td>${escapeHtml(row.car_name)}</td>
                    <td class="analytics-mono">${escapeHtml(row.plate_number)}</td>
                    <td>${formatNumber(row.total_bookings)}</td>
                    <td>${formatMoney(row.revenue)}</td>
                    <td>${formatMoney(row.expenses)}</td>
                    <td>${formatMoney(row.profit)}</td>
                    <td>${formatNumber(row.mileage)}</td>
                    <td>${formatMoney(row.revenue_per_km)}</td>
                    <td>${formatPercent(row.utilization_pct)}</td>
                    <td>${formatNumber(row.downtime_days)}</td>
                    <td>${escapeHtml(row.inspection_date)}</td>
                    <td>${escapeHtml(row.insurance_expiry)}</td>
                    <td>${formatNumber(row.last_service_mileage)}</td>
                    <td>${formatNumber(row.next_service_mileage)}</td>
                </tr>
            `).join("");

            $all("tr[data-car-id]", tableBody).forEach(row => {
                row.addEventListener("click", () => {
                    const query = buildQuery(queryState());
                    window.location.href = `/analytics/car/${row.dataset.carId}${query ? `?${query}` : ""}`;
                });
            });

            const totalPages = Math.max(Math.ceil(tableState.rows.length / tableState.pageSize), 1);
            paginationInfo.textContent = `Страница ${tableState.page} из ${totalPages} (${tableState.rows.length} строк)`;
            prevButton.disabled = tableState.page <= 1;
            nextButton.disabled = tableState.page >= totalPages;
        }

        function exportFleetRows(asExcel) {
            const rows = sortedRows();
            if (!rows.length) {
                return;
            }

            const columns = [
                ["Автомобиль", "car_name"],
                ["Госномер", "plate_number"],
                ["Бронирования", "total_bookings"],
                ["Выручка", "revenue"],
                ["Расходы", "expenses"],
                ["Прибыль", "profit"],
                ["Пробег", "mileage"],
                ["Выручка за км", "revenue_per_km"],
                ["Загрузка %", "utilization_pct"],
                ["Простой, дни", "downtime_days"],
                ["Техосмотр", "inspection_date"],
                ["Страховка", "insurance_expiry"],
                ["Последнее ТО, км", "last_service_mileage"],
                ["Следующее ТО, км", "next_service_mileage"],
            ];

            if (asExcel) {
                const html = `
                    <table>
                        <thead><tr>${columns.map(([label]) => `<th>${escapeHtml(label)}</th>`).join("")}</tr></thead>
                        <tbody>
                            ${rows.map(row => `<tr>${columns.map(([, key]) => `<td>${escapeHtml(row[key])}</td>`).join("")}</tr>`).join("")}
                        </tbody>
                    </table>
                `;
                downloadBlob("analitika-avtoparka.xls", html, "application/vnd.ms-excel");
                return;
            }

            const csv = [
                columns.map(([label]) => `"${label}"`).join(","),
                ...rows.map(row => columns.map(([, key]) => `"${String(row[key] ?? "").replaceAll('"', '""')}"`).join(",")),
            ].join("\n");
            downloadBlob("analitika-avtoparka.csv", csv, "text/csv;charset=utf-8");
        }

        async function loadFleetData() {
            loading.classList.remove("analytics-hidden");
            error.classList.add("analytics-hidden");
            try {
                const query = queryState();
                syncUrl("/analytics", query);
                const payload = await fetchJson(`/api/analytics?${buildQuery(query)}`);

                setCardValue(root, "total_revenue", payload.summary.total_revenue, "money");
                setCardValue(root, "total_bookings", payload.summary.total_bookings);
                setCardValue(root, "fleet_utilization_pct", payload.summary.fleet_utilization_pct, "percent");
                setCardValue(root, "total_mileage", payload.summary.total_mileage);
                setCardValue(root, "total_expenses", payload.summary.total_expenses, "money");
                setCardValue(root, "net_profit", payload.summary.net_profit, "money");
                setCardValue(root, "average_booking_value", payload.summary.average_booking_value, "money");
                setCardValue(root, "average_revenue_per_day", payload.summary.average_revenue_per_day, "money");
                setCardValue(root, "active_cars", payload.summary.active_cars);
                setCardValue(root, "service_soon", payload.summary.service_soon);

                renderChart("fleet-revenue-chart", payload.charts.revenue_over_time, "line", "Выручка", {moneyAxis: true, fill: true});
                renderChart("fleet-bookings-chart", payload.charts.booking_count_over_time, "bar", "Брони");
                renderChart("fleet-utilization-chart", payload.charts.fleet_utilization_trend, "line", "Загрузка", {percentAxis: true, borderColor: "#1f9d61", backgroundColor: "rgba(31, 157, 97, 0.16)"});
                renderChart("fleet-top-revenue-chart", payload.charts.top_cars_by_revenue, "bar", "Выручка", {moneyAxis: true});
                renderChart("fleet-top-bookings-chart", payload.charts.top_cars_by_bookings, "bar", "Брони", {borderColor: "#1f9d61", backgroundColor: "rgba(31, 157, 97, 0.2)"});
                renderDonutChart("fleet-expense-chart", payload.charts.expense_breakdown, "Расходы");

                tableState.rows = payload.table || [];
                tableState.page = 1;
                renderTable();
            } catch (loadError) {
                error.textContent = loadError.message;
                error.classList.remove("analytics-hidden");
                tableBody.innerHTML = "";
            } finally {
                loading.classList.add("analytics-hidden");
            }
        }

        periodField.addEventListener("change", toggleCustomFields);
        periodField.addEventListener("change", loadFleetData);
        startField.addEventListener("change", () => {
            if (periodField.value === "custom") {
                loadFleetData();
            }
        });
        endField.addEventListener("change", () => {
            if (periodField.value === "custom") {
                loadFleetData();
            }
        });
        applyButton.addEventListener("click", loadFleetData);
        resetButton.addEventListener("click", () => {
            periodField.value = root.dataset.defaultPeriod || "month";
            startField.value = root.dataset.defaultStart || "";
            endField.value = root.dataset.defaultEnd || "";
            carCheckboxes.forEach(input => {
                input.checked = false;
            });
            updateCarSummary();
            toggleCustomFields();
            loadFleetData();
        });
        carToggle.addEventListener("click", event => {
            event.stopPropagation();
            toggleCarMenu();
        });
        carMenu.addEventListener("click", event => {
            event.stopPropagation();
        });
        carCheckboxes.forEach(input => {
            input.addEventListener("change", () => {
                updateCarSummary();
                loadFleetData();
            });
        });
        document.addEventListener("click", event => {
            if (!carPicker.contains(event.target)) {
                closeCarMenu();
            }
        });
        prevButton.addEventListener("click", () => {
            if (tableState.page > 1) {
                tableState.page -= 1;
                renderTable();
            }
        });
        nextButton.addEventListener("click", () => {
            const totalPages = Math.max(Math.ceil(tableState.rows.length / tableState.pageSize), 1);
            if (tableState.page < totalPages) {
                tableState.page += 1;
                renderTable();
            }
        });
        csvButton.addEventListener("click", () => exportFleetRows(false));
        excelButton.addEventListener("click", () => exportFleetRows(true));

        $all("[data-sort-key]", root).forEach(button => {
            button.addEventListener("click", () => {
                const key = button.dataset.sortKey;
                if (tableState.sortKey === key) {
                    tableState.sortDir = tableState.sortDir === "asc" ? "desc" : "asc";
                } else {
                    tableState.sortKey = key;
                    tableState.sortDir = "desc";
                }
                tableState.page = 1;
                renderTable();
            });
        });

        toggleCustomFields();
        updateCarSummary();
        loadFleetData();
    }

    function renderCellValue(key, value) {
        if (value === null || value === undefined || value === "") {
            return "-";
        }
        if (key.includes("amount") || key === "revenue") {
            return formatMoney(value);
        }
        if (key.includes("mileage")) {
            return formatNumber(value);
        }
        return String(value);
    }

    function renderSimpleTable(tableId, rows, columns, emptyMessage) {
        const body = document.getElementById(tableId);
        if (!body) {
            return;
        }
        if (!rows?.length) {
            body.innerHTML = `<tr><td colspan="${columns.length}"><div class="analytics-empty">${escapeHtml(emptyMessage)}</div></td></tr>`;
            return;
        }

        body.innerHTML = rows.map(row => `
            <tr>${columns.map(column => `<td>${escapeHtml(renderCellValue(column, row[column]))}</td>`).join("")}</tr>
        `).join("");
    }

    function initCarPage() {
        const root = document.getElementById("car-analytics-root");
        if (!root) {
            return;
        }

        const carId = root.dataset.carId;
        const periodField = $("#car-filter-period", root);
        const startField = $("#car-filter-start-date", root);
        const endField = $("#car-filter-end-date", root);
        const carField = $("#car-filter-car-id", root);
        const applyButton = $("#car-filter-apply", root);
        const resetButton = $("#car-filter-reset", root);
        const csvButton = $("#car-export-csv", root);
        const excelButton = $("#car-export-excel", root);
        const loading = $("#car-loading", root);
        const error = $("#car-error", root);
        let lastPayload = null;

        function toggleCustomFields() {
            const isCustom = periodField.value === "custom";
            startField.closest(".analytics-filter").classList.toggle("analytics-hidden", !isCustom);
            endField.closest(".analytics-filter").classList.toggle("analytics-hidden", !isCustom);
        }

        function queryState() {
            return {
                period: periodField.value,
                start_date: startField.value,
                end_date: endField.value,
            };
        }

        function renderGallery(gallery) {
            const container = $("#car-gallery", root);
            if (!gallery?.length) {
                container.innerHTML = `<div class="analytics-empty">Фотографии автомобиля пока не добавлены.</div>`;
                return;
            }

            container.innerHTML = gallery.map(item => `
                <div class="analytics-gallery-card ${item.placeholder ? "placeholder" : ""}" data-label="${escapeHtml(item.title)}">
                    <div class="analytics-eyebrow">Фото автомобиля</div>
                    <h3>${escapeHtml(item.title)}</h3>
                    <div class="analytics-note">${escapeHtml(item.subtitle || "Fleet asset")}</div>
                </div>
            `).join("");
        }

        function renderMeta(meta) {
            setCardValue(root, "car_total_revenue", meta.total_revenue, "money");
            setCardValue(root, "car_total_expenses", meta.total_expenses, "money");
            setCardValue(root, "car_net_profit", meta.net_profit, "money");
            setCardValue(root, "car_roi_pct", meta.roi_pct, "percent");
            setCardValue(root, "car_average_booking_duration", meta.average_booking_duration);
            setCardValue(root, "car_average_daily_revenue", meta.average_daily_revenue, "money");
            setCardValue(root, "car_days_idle", meta.days_idle);
            const bestMonth = $('[data-summary-key="car_best_performing_month"]', root);
            if (bestMonth) {
                bestMonth.textContent = `${meta.best_performing_month} (${formatMoney(meta.best_performing_month_revenue)})`;
            }
            const currentMileage = $('[data-summary-key="car_current_mileage"]', root);
            if (currentMileage) {
                currentMileage.textContent = formatNumber(meta.current_mileage);
            }
            const statusBadge = $("#car-status-badge", root);
            if (meta.service_due && statusBadge) {
                statusBadge.className = "analytics-status service";
                statusBadge.textContent = "Нужно ТО";
            } else if (statusBadge) {
                statusBadge.className = "analytics-status available";
                statusBadge.textContent = "Аналитика автомобиля";
            }
        }

        async function loadCarData() {
            loading.classList.remove("analytics-hidden");
            error.classList.add("analytics-hidden");

            try {
                const query = queryState();
                syncUrl(`/analytics/car/${carId}`, query);
                const payload = await fetchJson(`/api/analytics/car/${carId}?${buildQuery(query)}`);
                lastPayload = payload;

                renderGallery(payload.gallery);

                renderMeta(payload.summary);

                renderChart("car-bookings-chart", payload.charts.booking_history, "bar", "Брони");
                renderChart("car-revenue-chart", payload.charts.revenue_history, "line", "Выручка", {moneyAxis: true, fill: true});
                renderChart("car-mileage-chart", payload.charts.mileage_growth, "line", "Пробег", {borderColor: "#1f9d61", backgroundColor: "rgba(31, 157, 97, 0.18)"});
                renderChart("car-expense-chart", payload.charts.expense_history, "bar", "Расходы", {moneyAxis: true, borderColor: "#cf3f3f", backgroundColor: "rgba(207, 63, 63, 0.18)"});

                renderSimpleTable("car-expenses-body", payload.tables.expenses, ["date", "category", "amount", "notes"], "История расходов пока пуста.");
                renderSimpleTable("car-service-body", payload.tables.service_history, ["date", "category", "amount", "mileage", "next_service_mileage", "notes"], "Записей по обслуживанию пока нет.");
                renderSimpleTable("car-tires-body", payload.tables.tire_history, ["date", "category", "amount", "mileage", "next_change_mileage", "notes"], "История замены шин пока пуста.");
                renderSimpleTable("car-insurance-body", payload.tables.insurance_history, ["date", "policy_number", "amount", "expiry_date", "notes"], "Платежей по страховке пока нет.");
                renderSimpleTable("car-fines-body", payload.tables.fine_history, ["date", "reference_number", "amount", "status", "notes"], "Штрафов пока нет.");
            } catch (loadError) {
                error.textContent = loadError.message;
                error.classList.remove("analytics-hidden");
            } finally {
                loading.classList.add("analytics-hidden");
            }
        }

        periodField.addEventListener("change", toggleCustomFields);
        periodField.addEventListener("change", loadCarData);
        startField.addEventListener("change", () => {
            if (periodField.value === "custom") {
                loadCarData();
            }
        });
        endField.addEventListener("change", () => {
            if (periodField.value === "custom") {
                loadCarData();
            }
        });
        applyButton.addEventListener("click", loadCarData);
        resetButton.addEventListener("click", () => {
            periodField.value = root.dataset.defaultPeriod || "month";
            startField.value = root.dataset.defaultStart || "";
            endField.value = root.dataset.defaultEnd || "";
            carField.value = carId;
            toggleCustomFields();
            loadCarData();
        });
        carField.addEventListener("change", () => {
            if (!carField.value) {
                return;
            }
            const query = buildQuery(queryState());
            window.location.href = `/analytics/car/${carField.value}${query ? `?${query}` : ""}`;
        });
        csvButton.addEventListener("click", () => {
            if (!lastPayload) {
                return;
            }
            const sections = [
                ["Расходы", lastPayload.tables.expenses, ["date", "category", "amount", "notes"]],
                ["История обслуживания", lastPayload.tables.service_history, ["date", "category", "amount", "mileage", "next_service_mileage", "notes"]],
                ["История шин", lastPayload.tables.tire_history, ["date", "category", "amount", "mileage", "next_change_mileage", "notes"]],
                ["Страхование", lastPayload.tables.insurance_history, ["date", "policy_number", "amount", "expiry_date", "notes"]],
                ["Штрафы", lastPayload.tables.fine_history, ["date", "reference_number", "amount", "status", "notes"]],
            ];
            const lines = [`"Section","${escapeHtml(root.dataset.carId)}"`];
            sections.forEach(([label, rows, columns]) => {
                lines.push(`"${label}"`);
                lines.push(columns.map(column => `"${column}"`).join(","));
                rows.forEach(row => {
                    lines.push(columns.map(column => `"${String(renderCellValue(column, row[column])).replaceAll('"', '""')}"`).join(","));
                });
                lines.push("");
            });
            downloadBlob(`avto-${carId}-analitika.csv`, lines.join("\n"), "text/csv;charset=utf-8");
        });
        excelButton.addEventListener("click", () => {
            if (!lastPayload) {
                return;
            }
            const sections = [
                ["Расходы", lastPayload.tables.expenses, ["date", "category", "amount", "notes"]],
                ["История обслуживания", lastPayload.tables.service_history, ["date", "category", "amount", "mileage", "next_service_mileage", "notes"]],
                ["История шин", lastPayload.tables.tire_history, ["date", "category", "amount", "mileage", "next_change_mileage", "notes"]],
                ["Страхование", lastPayload.tables.insurance_history, ["date", "policy_number", "amount", "expiry_date", "notes"]],
                ["Штрафы", lastPayload.tables.fine_history, ["date", "reference_number", "amount", "status", "notes"]],
            ];
            const html = sections.map(([label, rows, columns]) => `
                <h3>${escapeHtml(label)}</h3>
                <table>
                    <thead><tr>${columns.map(column => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead>
                    <tbody>${rows.map(row => `<tr>${columns.map(column => `<td>${escapeHtml(renderCellValue(column, row[column]))}</td>`).join("")}</tr>`).join("")}</tbody>
                </table>
            `).join("");
            downloadBlob(`avto-${carId}-analitika.xls`, html, "application/vnd.ms-excel");
        });

        toggleCustomFields();
        loadCarData();
    }

    document.addEventListener("DOMContentLoaded", () => {
        initFleetPage();
        initCarPage();
    });
})();
