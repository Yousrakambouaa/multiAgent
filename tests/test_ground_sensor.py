import unittest
from types import SimpleNamespace

from studio.flexible import FlexibleComponent, FlexibleObject, FlexibleProperty
from studio.generation import normalize_ground_check_anchors


class GroundSensorTests(unittest.TestCase):
    def test_authored_ray_origin_is_not_moved_inside_the_floor(self):
        actor = FlexibleObject(id="actor", name="Actor", position=(10, 5, 0), components=[
            FlexibleComponent(type="Sensor", properties=[FlexibleProperty(
                name="groundCheck", kind="reference", value="anchor:UnityEngine.Transform")]),
            FlexibleComponent(type="UnityEngine.BoxCollider2D", properties=[FlexibleProperty(
                name="size", kind="vector2", value="[1,1]")])])
        anchor = FlexibleObject(id="anchor", name="Ground Sensor", position=(10, 4.55, 0), parent="actor")
        plan = SimpleNamespace(scripts=SimpleNamespace(scripts=[SimpleNamespace(filename="Sensor.cs",
            content="Physics2D.Raycast(groundCheck.position, Vector2.down);")]),
            scenes=SimpleNamespace(scenes=[SimpleNamespace(name="Game", objects=[actor, anchor])]))
        self.assertEqual(normalize_ground_check_anchors(plan), [])
        self.assertGreater(anchor.position[1], actor.position[1] - .5)
