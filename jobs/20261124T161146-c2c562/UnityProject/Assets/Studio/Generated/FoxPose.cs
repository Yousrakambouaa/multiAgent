using UnityEngine;
using UnityEngine.Sprites;

[RequireComponent(typeof(SpriteRenderer))]
public class FoxPose : MonoBehaviour
{
    Mesh mesh;
    Vector3[] rest, points;
    Material ink;
    const int N = 20;
    Vector3 home;
    string pose;
    float age;
    public float Stretch => transform.localScale.y;
    public int ShownFacing => transform.localScale.x < 0 ? -1 : 1;

    void Start()
    {
        home = transform.localPosition;
        var sr = GetComponent<SpriteRenderer>();
        var s = sr.sprite;
        if (!s) return;
        var child = new GameObject("Animated fox artwork", typeof(MeshFilter), typeof(MeshRenderer));
        child.transform.SetParent(transform, false);
        mesh = new Mesh { name = "Fox pose lattice" };
        mesh.MarkDynamic();
        rest = new Vector3[(N + 1) * (N + 1)];
        points = new Vector3[rest.Length];
        var uv = new Vector2[rest.Length];
        var tris = new int[N * N * 6];
        var bounds = DataUtility.GetOuterUV(s);
        float aspect = s.rect.width / s.rect.height;
        float w = 1.68f * Mathf.Min(1, aspect), h = 1.68f * Mathf.Min(1, 1 / aspect);
        int t = 0;
        for (int j = 0; j <= N; j++) for (int i = 0; i <= N; i++)
        {
            int k = j * (N + 1) + i;
            float x = i / (float)N, y = j / (float)N;
            rest[k] = new Vector3((x - .5f) * w, (y - .5f) * h, 0);
            uv[k] = new Vector2(Mathf.Lerp(bounds.x, bounds.z, x), Mathf.Lerp(bounds.y, bounds.w, y));
            if (i < N && j < N) { tris[t++] = k; tris[t++] = k + N + 1; tris[t++] = k + 1; tris[t++] = k + 1; tris[t++] = k + N + 1; tris[t++] = k + N + 2; }
        }
        mesh.vertices = rest; mesh.uv = uv; mesh.triangles = tris;
        mesh.bounds = new Bounds(Vector3.zero, new Vector3(3, 3, .25f));
        child.GetComponent<MeshFilter>().sharedMesh = mesh;
        var renderer = child.GetComponent<MeshRenderer>();
        ink = new Material(Shader.Find("Sprites/Default"));
        ink.mainTexture = s.texture;
        renderer.sharedMaterial = ink;
        renderer.sortingOrder = sr.sortingOrder;
        sr.enabled = false;
    }

    public void Draw(string state, int face)
    {
        if (state != pose) { pose = state; age = 0; }
        age += Time.deltaTime;
        bool run = state == "Run";
        float wave = Mathf.Sin(age * 23);
        float sy = 1 + .025f * Mathf.Sin(age * 3.8f);
        float tilt = 0;
        if (state == "Jump") { sy = Mathf.Lerp(1.23f, 1.10f, Mathf.Clamp01(age / .30f)); tilt = 8; }
        else if (state == "Fall") { sy = .92f; tilt = -13; }
        else if (state == "Land") { float a = Mathf.Clamp01(age / .20f); sy = 1 - .25f * (1 - a) + .12f * Mathf.Sin(Mathf.PI * a); }
        else if (run) sy = 1 + .036f * wave;
        transform.localScale = new Vector3(face / sy, sy, 1);
        transform.localPosition = home + Vector3.up * ((sy - 1) * .55f + (run ? .085f * Mathf.Abs(wave) : 0));
        transform.localRotation = Quaternion.Euler(0, 0, face * tilt);
        if (!mesh) return;
        float blink = Mathf.Pow(Mathf.Max(0, Mathf.Cos((age - 1.5f) * 1.9f)), 100);
        for (int i = 0; i < rest.Length; i++)
        {
            var p = rest[i];
            if (run)
            {
                float heel = Mathf.Clamp01((-p.y - .06f) * 2.5f);
                float stride = wave * (p.x < 0 ? -1 : 1);
                p.x += heel * stride * .18f;
                p.y += heel * Mathf.Max(0, stride) * .13f;
            }
            else if (state == "Idle")
            {
                float x = p.x - .37f, y = p.y - .19f;
                p.y -= y * Mathf.Exp(-19 * x * x - 70 * y * y) * blink * .96f;
            }
            points[i] = p;
        }
        mesh.vertices = points;
    }

    void OnDestroy() { if (mesh) Destroy(mesh); if (ink) Destroy(ink); }
}