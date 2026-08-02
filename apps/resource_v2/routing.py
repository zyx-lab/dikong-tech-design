from django.urls import path

from apps.resource_v2.consumers import CameraResultsConsumer, DjiMqttConsumer


websocket_urlpatterns = [
    path("ws/v2/dji/mqtt", DjiMqttConsumer.as_asgi()),
    path("ws/v2/cameras/<int:id>/results", CameraResultsConsumer.as_asgi()),
]
