import os
import processing, statistics

from qgis.PyQt import (
    QtWidgets,
    QtCore
)
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsField,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest
)
from qgis.PyQt.QtCore import QVariant


class AnomalyPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.dlg = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        self.action = QtWidgets.QAction(QIcon(icon_path), 'Anomaly Tools', self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        # Add to Plugins menu
        self.iface.addPluginToMenu('&AnomalyTools', self.action)

        # Add to toolbar (quick panel)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginMenu('&AnomalyTools', self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        if self.dlg is None:
            self.dlg = PluginDialog(self.iface)
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

class PluginDialog(QtWidgets.QDialog):
    # Order here must match the QGIS "qgis:zonalstatistics" STATISTICS enum:
    # Count, Sum, Mean, Median, St dev, Min, Max, Range, Minority, Majority, Variety, Variance, All
    RASTER_STATS = [
        ('Mean', 2),
        ('Median', 3),
        ('St dev', 4),
        ('Min', 5),
        ('Max', 6),
        ('Range', 7),
    ]

    DEFAULTS = {
        'area_min': 1.5, 'area_max': 2.0,
        'ratio_min': 1.1, 'ratio_max': 1.6,
        'raster_stat_min': -1e9, 'raster_stat_max': 0.5,
    }

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle('Anomaly Tools')
        self.resize(700, 460)
        self.layout = QtWidgets.QVBoxLayout()
        self.setLayout(self.layout)

        self.tabs = QtWidgets.QTabWidget()
        self.layout.addWidget(self.tabs)

        self.tab1 = QtWidgets.QWidget()
        self.tab2 = QtWidgets.QWidget()
        self.tabs.addTab(self.tab1, 'Compute Stats')
        self.tabs.addTab(self.tab2, 'Filter Features')

        # Name of the raster statistic field actually produced by the last
        # Tab 1 run (discovered dynamically - see _run_tab1). Filtering in
        # Tab 2 always uses this, so the tool works with any raster/band/
        # statistic combination rather than a hardcoded field name.
        self.raster_stat_field = None
        self.latest_stats = None

        self._build_tab1()
        self._build_tab2()

    def _layer_names(
        self, 
        layer_type: str ='vector'
    ) -> str:
        if layer_type == 'vector':
            layers = [
                l for l in QgsProject.instance().mapLayers().values() 
                if isinstance(l, QgsVectorLayer)
            ]

        else:
            layers = [
                l for l in QgsProject.instance().mapLayers().values() 
                if isinstance(l, QgsRasterLayer)
            ]

        return [l.name() for l in layers]

    def _get_layer_by_name(
        self, 
        name: str, 
        layer_type: str ='vector'
    ) -> str:
        if layer_type == 'vector':
            layers = QgsProject.instance().mapLayersByName(name)
        else:
            layers = [
                l for l in QgsProject.instance().mapLayers().values() 
                if isinstance(l, QgsRasterLayer) and l.name() == name
            ]
        
        return layers[0] if layers else None

    # ------------------------------------------------------------------
    # Tab 1: Compute Stats
    # ------------------------------------------------------------------
    def _build_tab1(self):
        layout = QtWidgets.QFormLayout()
        self.tab1.setLayout(layout)

        self.vec_combo = QtWidgets.QComboBox()
        self.vec_combo.addItems(self._layer_names('vector'))
        self.vec_refresh = QtWidgets.QPushButton('Refresh layers')
        self.vec_refresh.clicked.connect(self._refresh_layer_lists)
        hvec = QtWidgets.QHBoxLayout()
        hvec.addWidget(self.vec_combo)
        hvec.addWidget(self.vec_refresh)
        layout.addRow('Input vector layer:', hvec)

        # Any raster layer can be used here: a DEM, a spectral index, a
        # model confidence surface, etc. Band + statistic are chosen below.
        self.rast_combo = QtWidgets.QComboBox()
        self.rast_combo.addItems(self._layer_names('raster'))
        self.rast_combo.currentIndexChanged.connect(self._update_band_range)
        hr = QtWidgets.QHBoxLayout()
        hr.addWidget(self.rast_combo)
        layout.addRow('Input raster layer:', hr)

        self.band_spin = QtWidgets.QSpinBox()
        self.band_spin.setMinimum(1)
        self.band_spin.setMaximum(1)
        layout.addRow('Raster band:', self.band_spin)
        self._update_band_range()

        self.stat_combo = QtWidgets.QComboBox()
        self.stat_combo.addItems([name for name, _ in self.RASTER_STATS])
        self.stat_combo.setCurrentText('Range')  # preserves previous default behaviour
        layout.addRow('Raster statistic per polygon:', self.stat_combo)

        hint = QtWidgets.QLabel(
            'Tip: for a DEM used to estimate anomaly depth, "Min" gives the lowest\n'
            'elevation under each polygon (deepest point); "Range" gives local relief.'
        )
        hint.setStyleSheet('color: gray;')
        layout.addRow(hint)

        self.run_btn = QtWidgets.QPushButton('Run: Compute Stats + Zonal Stats')
        self.run_btn.clicked.connect(self._run_tab1)
        layout.addRow(self.run_btn)

        self.save_btn = QtWidgets.QPushButton('Save Report as TXT')
        self.save_btn.clicked.connect(self._save_report)
        layout.addRow(self.save_btn)

    def _update_band_range(self):
        rlayer = self._get_layer_by_name(self.rast_combo.currentText(), 'raster')
        band_count = rlayer.bandCount() if rlayer else 1
        self.band_spin.setMaximum(max(band_count, 1))
        if self.band_spin.value() > band_count:
            self.band_spin.setValue(1)

    # ------------------------------------------------------------------
    # Tab 2: Filter Features
    # ------------------------------------------------------------------
    def _build_tab2(self):
        layout = QtWidgets.QFormLayout()
        self.tab2.setLayout(layout)

        self.filter_vec_combo = QtWidgets.QComboBox()
        self.filter_vec_combo.addItems(self._layer_names('vector'))
        self.filter_refresh = QtWidgets.QPushButton('Refresh layers')
        self.filter_refresh.clicked.connect(self._refresh_layer_lists)
        hvec2 = QtWidgets.QHBoxLayout()
        hvec2.addWidget(self.filter_vec_combo)
        hvec2.addWidget(self.filter_refresh)
        layout.addRow('Input vector layer:', hvec2)

        self.area_min = QtWidgets.QDoubleSpinBox()
        self.area_min.setDecimals(3)
        self.area_min.setRange(-1e9, 1e9)
        
        self.area_max = QtWidgets.QDoubleSpinBox()
        self.area_max.setDecimals(3)
        self.area_max.setRange(-1e9, 1e9)
        
        self.ratio_min = QtWidgets.QDoubleSpinBox()
        self.ratio_min.setDecimals(3)
        self.ratio_min.setRange(-1e9, 1e9)
        
        self.ratio_max = QtWidgets.QDoubleSpinBox()
        self.ratio_max.setDecimals(3)
        self.ratio_max.setRange(-1e9, 1e9)
        
        self.raster_stat_min = QtWidgets.QDoubleSpinBox()
        self.raster_stat_min.setDecimals(4)
        self.raster_stat_min.setRange(-1e9, 1e9)
        
        self.raster_stat_max = QtWidgets.QDoubleSpinBox()
        self.raster_stat_max.setDecimals(4)
        self.raster_stat_max.setRange(-1e9, 1e9)

        layout.addRow('Area (min):', self.area_min)
        layout.addRow('Area (max):', self.area_max)
        layout.addRow('Ratio (min):', self.ratio_min)
        layout.addRow('Ratio (max):', self.ratio_max)

        # This label is updated dynamically once Tab 1 has run, so it always
        # reflects the actual field name / statistic being filtered on.
        self.raster_stat_label_min = QtWidgets.QLabel('Raster stat (min):')
        self.raster_stat_label_max = QtWidgets.QLabel('Raster stat (max):')
        layout.addRow(self.raster_stat_label_min, self.raster_stat_min)
        layout.addRow(self.raster_stat_label_max, self.raster_stat_max)

        self.default_btn = QtWidgets.QPushButton('Use Default Values')
        self.default_btn.clicked.connect(self._use_defaults)
        layout.addRow(self.default_btn)

        self.filter_run_btn = QtWidgets.QPushButton('Run Filter & Create Layer')
        self.filter_run_btn.clicked.connect(self._run_tab2)
        layout.addRow(self.filter_run_btn)

        self._use_defaults()

    def _refresh_layer_lists(self):
        vecs = self._layer_names('vector')
        rasts = self._layer_names('raster')
        for cb in (self.vec_combo, self.filter_vec_combo):
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(vecs)
            cb.blockSignals(False)
        self.rast_combo.blockSignals(True)
        self.rast_combo.clear()
        self.rast_combo.addItems(rasts)
        self.rast_combo.blockSignals(False)
        self._update_band_range()

    def _run_tab1(self):
        vec_name = self.vec_combo.currentText()
        rast_name = self.rast_combo.currentText()
        vlayer = self._get_layer_by_name(vec_name, 'vector')
        rlayer = self._get_layer_by_name(rast_name, 'raster')
        if not vlayer or not rlayer:
            QtWidgets.QMessageBox.warning(
                self, 'Missing layer', 'Please select both vector and raster layers.'
            )
            
            return

        band = self.band_spin.value()
        stat_name, stat_index = self.RASTER_STATS[self.stat_combo.currentIndex()]

        # --- Run original script logic: create _stat layer
        base_name = vlayer.name()
        output_layer_name = f"{base_name}_stat"
        vlayer_stat = QgsVectorLayer(vlayer.dataProvider().dataSourceUri(), output_layer_name, 'ogr')
        QgsProject.instance().addMapLayer(vlayer_stat)

        # Add fields if missing
        fields = vlayer_stat.fields()
        for f_name in ['Length', 'Width', 'Area', 'Ratio']:
            if fields.indexOf(f_name) == -1:
                vlayer_stat.dataProvider().addAttributes([QgsField(f_name, QVariant.Double)])
        vlayer_stat.updateFields()
        vlayer_stat.startEditing()

        expr_length = QgsExpression(
            'if(num_points($geometry)=5,'
            'array_max(array(distance(point_n($geometry,1),'
            'point_n($geometry,2)),'
            'distance(point_n($geometry,2),point_n($geometry,3)))),0)'
        )
        expr_width = QgsExpression(
            'if(num_points($geometry)=5,'
            'array_min(array(distance(point_n($geometry,1),'
            'point_n($geometry,2)),'
            'distance(point_n($geometry,2),point_n($geometry,3)))),0)'
        )
        context = QgsExpressionContext()
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(vlayer_stat))
        idxs = {
            f: vlayer_stat.fields().indexOf(f) 
            for f in ['Length', 'Width', 'Area', 'Ratio']
        }

        for feat in vlayer_stat.getFeatures():
            context.setFeature(feat)
            length_val = float(expr_length.evaluate(context) or 0)
            width_val = float(expr_width.evaluate(context) or 0)
            area_val = length_val * width_val
            ratio_val = length_val / width_val if width_val != 0 else 0
            vlayer_stat.changeAttributeValue(feat.id(), idxs['Length'], length_val)
            vlayer_stat.changeAttributeValue(feat.id(), idxs['Width'], width_val)
            vlayer_stat.changeAttributeValue(feat.id(), idxs['Area'], area_val)
            vlayer_stat.changeAttributeValue(feat.id(), idxs['Ratio'], ratio_val)

        vlayer_stat.commitChanges()

        # Clean up old zonal stats fields from any previous run
        fields_to_remove = [
            f.name() for f in vlayer_stat.fields() 
            if f.name().startswith('raster_') or f.name().startswith('zs_')
        ]

        if fields_to_remove:
            vlayer_stat.startEditing()
            for fn in fields_to_remove:
                idx = vlayer_stat.fields().indexOf(fn)
                if idx != -1:
                    vlayer_stat.deleteAttribute(idx)
            vlayer_stat.updateFields()
            vlayer_stat.commitChanges()

        # Run zonal statistics with the user-selected band + statistic.
        # We don't assume what the resulting field will be called: OGR/QGIS
        # may truncate it (e.g. shapefile's 10-char field limit turns
        # "raster_range" into "raster_ran"), and different statistics/output
        # formats produce different names. So we diff the field list before
        # and after the algorithm runs to find out exactly what was added.
        fields_before = {f.name() for f in vlayer_stat.fields()}
        processing.run("qgis:zonalstatistics", {
            'INPUT_RASTER': rlayer,
            'RASTER_BAND': band,
            'INPUT_VECTOR': vlayer_stat,
            'COLUMN_PREFIX': 'raster_',
            'STATISTICS': [stat_index],
        })
        vlayer_stat.updateFields()
        fields_after = {f.name() for f in vlayer_stat.fields()}
        new_fields = list(fields_after - fields_before)

        if len(new_fields) == 1:
            self.raster_stat_field = new_fields[0]
        
        elif new_fields:
            # Unexpected but not fatal: fall back to the first new field.
            self.raster_stat_field = sorted(new_fields)[0]
        
        else:
            QtWidgets.QMessageBox.warning(
                self, 'Zonal statistics',
                'Zonal statistics did not add a new field - filtering on the raster '
                'statistic will not be available until this succeeds.'
            )
            
            self.raster_stat_field = None

        # Reflect the actual field name in Tab 2's labels
        if self.raster_stat_field:
            label = f'Raster {stat_name} [{self.raster_stat_field}] (band {band}) (min):'
            label_max = f'Raster {stat_name} [{self.raster_stat_field}] (band {band}) (max):'
            
            self.raster_stat_label_min.setText(label)
            self.raster_stat_label_max.setText(label_max)

        # --- Compute stats for Length, Width, Area, Ratio, and the raster stat field ---
        stats = {}
        field_names = ['Length', 'Width', 'Area', 'Ratio']
        if self.raster_stat_field:
            field_names.append(self.raster_stat_field)
        for f in field_names:
            if vlayer_stat.fields().indexOf(f) != -1:
                vals = [
                    feat[f] for feat in vlayer_stat.getFeatures() 
                    if isinstance(feat[f], (int, float))
                ]
                
                if vals:
                    stats[f] = {
                        'min': min(vals), 
                        'max': max(vals), 
                        'mean': statistics.mean(vals), 
                        'stdev': statistics.pstdev(vals)
                    }

        self.latest_stats = stats

        # Show popup
        msg = ''
        
        for k, v in stats.items():
            msg += (
                f"{k} - Min: {v['min']:.4f}, "
                f"Max: {v['max']:.4f}, "
                f"Mean: {v['mean']:.4f}, "
                f"StdDev: {v['stdev']:.4f}\n"
            )
        
        QtWidgets.QMessageBox.information(self, 'Statistics', msg)

    def _save_report(self):
        if not self.latest_stats:
            QtWidgets.QMessageBox.warning(
                self, 
                'No stats', 
                'Run Tab1 first to generate statistics.'
            )

            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 
            'Save Report as TXT', '', 
            'Text Files (*.txt)'
        )

        if path:
            with open(path, 'w') as f:
                for k, v in self.latest_stats.items():
                    f.write(
                        f"{k} - Min: {v['min']:.4f}, "
                        f"Max: {v['max']:.4f}, "
                        f"Mean: {v['mean']:.4f}, "
                        f"StdDev: {v['stdev']:.4f}\n"
                    )

            QtWidgets.QMessageBox.information(self, 'Saved', f'Report saved to {path}')

    def _use_defaults(self):
        self.area_min.setValue(self.DEFAULTS['area_min'])
        self.area_max.setValue(self.DEFAULTS['area_max'])
        self.ratio_min.setValue(self.DEFAULTS['ratio_min'])
        self.ratio_max.setValue(self.DEFAULTS['ratio_max'])
        self.raster_stat_min.setValue(self.DEFAULTS['raster_stat_min'])
        self.raster_stat_max.setValue(self.DEFAULTS['raster_stat_max'])

    def _run_tab2(self):
        name = self.filter_vec_combo.currentText()
        vlayer = self._get_layer_by_name(name, 'vector')
        if not vlayer:
            QtWidgets.QMessageBox.warning(self, 'Missing layer', 'Select a vector layer.')
            return

        a_min, a_max = self.area_min.value(), self.area_max.value()
        r_min, r_max = self.ratio_min.value(), self.ratio_max.value()

        expr_parts = [
            f'("Area">={a_min} AND "Area"<={a_max})',
            f'("Ratio">={r_min} AND "Ratio"<={r_max})',
        ]

        raster_field = self.raster_stat_field
        if raster_field and vlayer.fields().indexOf(raster_field) != -1:
            rs_min, rs_max = self.raster_stat_min.value(), self.raster_stat_max.value()
            expr_parts.append(f'("{raster_field}">={rs_min} AND "{raster_field}"<={rs_max})')
        else:
            QtWidgets.QMessageBox.information(
                self, 'No raster statistic available',
                'Run "Compute Stats" first (or ensure the selected layer has a raster '
                'statistic field) to also filter on the raster values. Continuing with '
                'Area and Ratio only.'
            )

        expr = ' AND '.join(expr_parts)
        vlayer.removeSelection()
        vlayer.selectByExpression(expr)
        count = vlayer.selectedFeatureCount()
        if count == 0:
            QtWidgets.QMessageBox.information(self, 'No matches', 'No features matched filter.')
            return
        req = QgsFeatureRequest().setFilterFids(vlayer.selectedFeatureIds())
        selected_layer = vlayer.materialize(req)
        selected_layer.setName(f"{vlayer.name()}_filtered")
        QgsProject.instance().addMapLayer(selected_layer)
        QtWidgets.QMessageBox.information(
            self, 
            'Done', 
            f'Added filtered layer "{selected_layer.name()}" with {count} features.'
        )
