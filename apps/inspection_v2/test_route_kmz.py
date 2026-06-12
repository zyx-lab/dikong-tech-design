import zipfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework import serializers

from apps.inspection_v2.models import WaylineType
from apps.inspection_v2.route_kmz import parse_route_kmz


class RouteKmzParserTests(SimpleTestCase):
    def kmz_file(self, *, template_type="mapping2d", include_template=True, include_waylines=True):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            if include_template:
                archive.writestr(
                    "wpmz/template.kml",
                    f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document><Folder><wpml:templateType>{template_type}</wpml:templateType></Folder></Document>
</kml>
""",
                )
            if include_waylines:
                archive.writestr(
                    "wpmz/waylines.wpml",
                    """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.6">
  <Document><Folder><wpml:autoFlightSpeed>8.50</wpml:autoFlightSpeed>
    <Placemark>
      <Point><coordinates>121.47370000,31.23040000</coordinates></Point>
      <wpml:index>0</wpml:index>
      <wpml:executeHeight>120.00</wpml:executeHeight>
      <wpml:waypointSpeed>8.50</wpml:waypointSpeed>
      <wpml:waypointHeadingParam><wpml:waypointHeadingAngle>90.00</wpml:waypointHeadingAngle></wpml:waypointHeadingParam>
    </Placemark>
    <Placemark>
      <Point><coordinates>121.47470000,31.23140000</coordinates></Point>
      <wpml:index>1</wpml:index>
      <wpml:executeHeight>120.00</wpml:executeHeight>
      <wpml:waypointSpeed>8.50</wpml:waypointSpeed>
      <wpml:waypointHeadingParam><wpml:waypointHeadingAngle>180.00</wpml:waypointHeadingAngle></wpml:waypointHeadingParam>
    </Placemark>
  </Folder></Document>
</kml>
""",
                )
        return SimpleUploadedFile("route.kmz", buffer.getvalue(), content_type="application/vnd.google-earth.kmz")

    def test_parse_route_kmz_should_extract_wayline_type_and_waypoints(self):
        parsed = parse_route_kmz(self.kmz_file(template_type="mapping2d"))

        self.assertEqual(parsed.wayline_type, WaylineType.MAPPING_2D)
        self.assertEqual(str(parsed.default_altitude), "120.00")
        self.assertEqual(str(parsed.default_speed), "8.50")
        self.assertEqual(len(parsed.waypoints), 2)
        self.assertEqual(parsed.waypoints[0]["sequence"], 1)
        self.assertEqual(str(parsed.waypoints[0]["latitude"]), "31.23040000")
        self.assertEqual(str(parsed.waypoints[0]["longitude"]), "121.47370000")
        self.assertEqual(str(parsed.waypoints[0]["heading"]), "90.00")

    def test_parse_route_kmz_should_reject_missing_wpml_files(self):
        with self.assertRaises(serializers.ValidationError) as context:
            parse_route_kmz(self.kmz_file(include_template=False))

        self.assertIn("wpmz/template.kml", str(context.exception.detail))

    def test_parse_route_kmz_should_reject_unknown_template_type(self):
        with self.assertRaises(serializers.ValidationError) as context:
            parse_route_kmz(self.kmz_file(template_type="unknown"))

        self.assertIn("templateType", str(context.exception.detail))
