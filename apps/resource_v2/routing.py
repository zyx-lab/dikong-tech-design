from django.urls import path

from apps.resource_v2.consumers import DjiMqttConsumer


websocket_urlpatterns = [
    path("ws/v2/dji/mqtt", DjiMqttConsumer.as_asgi()),
]
