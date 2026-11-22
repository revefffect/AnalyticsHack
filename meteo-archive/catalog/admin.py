from django.contrib import admin

from .models import Dataset, Location, Station


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "kind")
    search_fields = ("name", "region")
    list_filter = ("kind",)


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "profile")
    search_fields = ("name", "location__name", "location__region")
    list_filter = ("location", "profile")


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("original_name", "station", "row_count", "uploaded_by", "uploaded_at")
    search_fields = ("original_name", "station__name", "uploaded_by__username")
    readonly_fields = (
        "original_name", "station", "row_count", "first_observation", "last_observation",
        "metrics", "uploaded_by", "uploaded_at", "sha256",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("station", "uploaded_by").defer("contents")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
