def classFactory(iface):
    from .anomaly_plugin import AnomalyPlugin
    return AnomalyPlugin(iface)
