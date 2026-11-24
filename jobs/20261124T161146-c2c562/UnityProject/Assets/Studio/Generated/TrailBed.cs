using UnityEngine;

[RequireComponent(typeof(SpriteRenderer))]
public class TrailBed : MonoBehaviour
{
    public int tiles = 4;
    public float tileWidth = 2;
    public float tileHeight = 1;
    void Awake()
    {
        var source = GetComponent<SpriteRenderer>();
        var tile = source ? source.sprite : null;
        if (!tile) return;
        source.enabled = false;
        for (int i = 0; i < tiles; i++)
        {
            var r = new GameObject("Grassy platform tile").AddComponent<SpriteRenderer>();
            r.transform.SetParent(transform, false);
            r.transform.localPosition = new Vector3(tileWidth * (i - (tiles - 1) * .5f), 0, 0);
            r.sprite = tile;
            r.transform.localScale = new Vector3((tileWidth + .025f) / tile.bounds.size.x, tileHeight / tile.bounds.size.y, 1);
        }
    }
}