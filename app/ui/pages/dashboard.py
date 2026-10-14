from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView
from app.ui.widgets.common import Card, Chart, Pills, label, button, row
from app.i18n import tr


class Dashboard(QWidget):
    def __init__(self, open_fixer):
        super().__init__()
        box = QVBoxLayout(self)
        box.setSpacing(14)
        grid = QGridLayout()
        grid.setSpacing(14)
        total = Card('Total converted today', True)
        self.converted = label('0.00 GB', 'number')
        self.files = label('0 files · verified output', 'muted')
        total.box.addWidget(self.converted)
        total.box.addWidget(self.files)
        grid.addWidget(total, 0, 0)
        insight = Card('TV-Compatibility Check', True)
        insight.box.addWidget(label('One clean frame rate. Every screen.', 'muted'))
        insight.box.addStretch()
        insight.box.addWidget(button('Analyze & Fix MP4s', open_fixer, True))
        grid.addWidget(insight, 1, 0)
        queue = Card('Live queue')
        queue.box.addWidget(Pills(['All', 'Waiting', 'Done']))
        self.queue_table = QTableWidget(0, 2)
        self.queue_table.setHorizontalHeaderLabels(['File', 'Status'])
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.queue_table.verticalHeader().hide()
        self.queue_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.queue_table.setMinimumHeight(180)
        queue.box.addWidget(self.queue_table)
        grid.addWidget(queue, 0, 1, 2, 1)
        performance = Card('Studio performance')
        tiles = QGridLayout()
        self.metrics = {}
        for i, name in enumerate(['Avg speed', 'GPU load', 'CPU load', 'Space saved']):
            tile = Card()
            tile.box.setContentsMargins(11, 12, 11, 12)
            value = label('—', 'title')
            tile.box.addWidget(value)
            tile.box.addWidget(label(name, 'muted'))
            tiles.addWidget(tile, i // 2, i % 2)
            self.metrics[name] = value
        performance.box.addLayout(tiles)
        grid.addWidget(performance, 0, 2, 2, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 3)
        box.addLayout(grid)
        chart_card = Card('Encoding performance')
        self.series = Pills(['Speed', 'CPU', 'GPU'], 'Speed')
        self.ranges = Pills(['1M', '5M', '15M'], '5M')
        chart_card.box.addLayout(row(self.series, None, self.ranges))
        self.chart = Chart(1800)
        chart_card.box.addWidget(self.chart)
        chart_card.setMinimumHeight(235)
        box.addWidget(chart_card, 1)
        self.queue_filter = 'All'
        queue.box.itemAt(1).widget().selected.connect(self.set_filter)
        self.last_jobs = []

    def set_filter(self, value):
        self.queue_filter = value
        self.update_jobs(self.last_jobs)

    def update_jobs(self, jobs):
        self.last_jobs = jobs
        filtered = [j for j in jobs if self.queue_filter == 'All' or j.status == self.queue_filter
                    or (self.queue_filter == 'Done' and j.status == 'TV-Safe ✓')][-8:]
        self.queue_table.setRowCount(len(filtered))
        for i, job in enumerate(filtered):
            self.queue_table.setItem(i, 0, QTableWidgetItem(job.source.name))
            self.queue_table.setItem(i, 1, QTableWidgetItem(f'{tr(job.status)}: {job.data.get("reason","")}  {job.data.get("progress", 0):.0f}%'))
        done = [j for j in jobs if j.status in ('TV-Safe ✓', 'Done') and j.data.get('output')]
        size = sum(j.data.get('size', 0) for j in done)
        self.converted.setText(f'{size / 1e9:.2f} GB')
        self.files.setText(f'{len(done)} files · verified output')
        speeds = [j.data.get('speed', 0) for j in jobs if j.status == 'Encoding']
        self.metrics['Avg speed'].setText(f'{sum(speeds)/len(speeds):.2f}×' if speeds else '—')
        saved = 0
        for job in done:
            try:
                saved += int(job.media.format.get('size') or 0) - job.data.get('size', 0) if job.media else 0
            except OSError:
                pass
        self.metrics['Space saved'].setText(f'{saved / 1e9:+.2f} GB')
