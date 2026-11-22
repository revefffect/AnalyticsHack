from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from catalog import assistant, external, grafana, monitor_views, platform_views, streaming, views


urlpatterns = [
    path("", views.catalog, name="catalog"),
    path("places/new/", views.add_location, name="add-location"),
    path("stations/new/", views.add_station, name="add-station"),
    path("stations/<int:pk>/", views.station, name="station"),
    path("api/stations.geojson", views.station_geojson, name="station-geojson"),
    path("stations/<int:pk>/upload/", platform_views.new_import, name="upload"),
    path("imports/<uuid:key>/", platform_views.preview_import, name="preview-import"),
    path("datasets/", platform_views.datasets, name="datasets"),
    path("business/", platform_views.business, name="business"),
    path("sources/open-meteo/", external.connect, name="external-source"),
    path("alerts/", monitor_views.inbox, name="alerts"),
    path("stations/<int:pk>/stream/", monitor_views.add_stream, name="add-stream"),
    path("streams/<uuid:key>/", monitor_views.stream_detail, name="stream-detail"),
    path("streams/<uuid:key>/grafana/", grafana.open_stream, name="stream-grafana"),
    path("api/streams/<uuid:key>/observations/", streaming.ingest, name="stream-ingest"),
    path("datasets/<uuid:version>/", platform_views.report, name="dataset-report"),
    path("datasets/<uuid:version>/assistant/", assistant.analyze_slice, name="dataset-assistant"),
    path("datasets/<uuid:version>/share/", platform_views.share, name="dataset-share"),
    path("datasets/<uuid:version>/grafana/", grafana.open_dashboard, name="grafana-open"),
    path("internal/grafana-auth/", grafana.proxy_identity, name="grafana-proxy-auth"),
    path("datasets/<uuid:version>/save/", platform_views.save_report, name="save-report"),
    path("reports/<uuid:key>/", platform_views.saved_report, name="saved-report"),
    path("assets/plotly.js", platform_views.plotly_js, name="plotly-js"),
    path("datasets/<int:pk>/download/", views.download, name="download"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("health/", views.health, name="health"),
    path("admin/", admin.site.urls),
]
