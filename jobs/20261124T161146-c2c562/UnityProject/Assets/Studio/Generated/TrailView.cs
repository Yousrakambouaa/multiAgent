using UnityEngine;

public class TrailView : MonoBehaviour
{
    public Transform fox, forest, motes;
    float velocity, origin;
    Vector3 forestHome, moteHome;
    public float CameraX => transform.position.x;

    void Start()
    {
        origin = transform.position.x;
        forestHome = forest.position;
        moteHome = motes.position;
        for (int i = 0; i < 38; i++)
        {
            var sr = new GameObject("Forest pollen").AddComponent<SpriteRenderer>();
            sr.transform.SetParent(motes, false);
            sr.transform.localPosition = new Vector3(-12 + i * .83f, -.4f + Mathf.Repeat(i * 1.618f, 5.8f), 0);
            sr.transform.localScale = Vector3.one * (.038f + (i % 4) * .017f);
            sr.sprite = Burst.Glint;
            sr.sortingOrder = -7;
            sr.color = new Color(1, .96f, .66f, .27f + (i % 3) * .07f);
        }
    }

    void LateUpdate()
    {
        var p = transform.position;
        p.x = Mathf.SmoothDamp(p.x, Mathf.Clamp(fox.position.x + 2, 1, 24), ref velocity, .24f);
        transform.position = p;
        float offset = p.x - origin;
        forest.position = forestHome + Vector3.right * (offset * .72f);
        motes.position = moteHome + Vector3.right * (offset * .34f);
    }
}