using UnityEngine;
using UnityEngine.UI;
using UnityEngine.SceneManagement;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
namespace Studio.Runtime {
    public class GameDirector : MonoBehaviour {
        [System.Serializable] private class CollisionConfig { public CollisionPair[] collision_rules; }
        [System.Serializable] private class CollisionPair { public int layer_a,layer_b; public bool ignore; }
        public string title, subtitle, firstScene, menuScene, resultScene, nextScene, sceneKind, playLabel = "Play";
        public int touchButtonSize = 100;
        public int referenceWidth = 1280, referenceHeight = 720;
        public Color accentColor = new Color(.96f,.74f,.35f);
        public bool buildUI = true;
        public int Score { get; private set; }
        public int Coins { get; private set; }
        public static int LastScore;
        private Canvas canvas;
        private Text scoreText, hint;
        private GameObject pausePanel;
        private Font font;
        void Awake() {
            var asset=Resources.Load<TextAsset>("studio_scenes");
            if(asset!=null) {
                var config=JsonUtility.FromJson<CollisionConfig>(asset.text);
                if(config.collision_rules!=null) foreach(var pair in config.collision_rules) Physics2D.IgnoreLayerCollision(pair.layer_a,pair.layer_b,pair.ignore);
            }
            Application.targetFrameRate=60;
        }
        void Start() {
            Time.timeScale = 1; TouchPad.ResetState();
            var player = FindFirstObjectByType<PlayerMotor>();
            var rules = GetComponent<LevelRules>();
            if (player != null && rules != null) rules.Configure(player);
            if (buildUI) CreateUI();
        }
        public void AddCoin() {
            var rules = GetComponent<LevelRules>();
            Coins++; Score += rules == null ? 10 : Mathf.Max(1, rules.CoinPoints());
            if (scoreText != null) scoreText.text = $"COINS {Coins:00}     SCORE {Score:0000}";
        }
        public void ShowHint(string text) { if (hint != null) hint.text = text; }
        public void CompleteLevel() { LastScore = Score; Time.timeScale = 1; SceneManager.LoadScene(nextScene); }
        public void Play() { LastScore = 0; Time.timeScale = 1; SceneManager.LoadScene(firstScene); }
        public void Restart() { Time.timeScale = 1; SceneManager.LoadScene(SceneManager.GetActiveScene().name); }
        public void Menu() { Time.timeScale = 1; SceneManager.LoadScene(menuScene); }
        public void Pause() { Time.timeScale = Time.timeScale == 0 ? 1 : 0; if (pausePanel != null) pausePanel.SetActive(Time.timeScale == 0); TouchPad.ResetState(); }
        private RectTransform Rect(GameObject obj, Vector2 anchor, Vector2 position, Vector2 size) {
            var rect = obj.GetComponent<RectTransform>(); rect.anchorMin = rect.anchorMax = anchor; rect.anchoredPosition = position;
            float width=canvas.GetComponent<RectTransform>().rect.width;
            if(width>40) size.x=Mathf.Min(size.x,width-24);
            rect.sizeDelta = size; return rect;
        }
        private GameObject Panel(string name, Vector2 anchor, Vector2 pos, Vector2 size, Color color) {
            var obj = new GameObject(name, typeof(RectTransform), typeof(Image)); obj.transform.SetParent(canvas.transform, false);
            Rect(obj, anchor, pos, size); obj.GetComponent<Image>().color = color; return obj;
        }
        private Text Label(string name, string value, Vector2 anchor, Vector2 pos, Vector2 size, int fontSize) {
            var obj = new GameObject(name, typeof(RectTransform), typeof(Text)); obj.transform.SetParent(canvas.transform, false);
            Rect(obj, anchor, pos, size); var text = obj.GetComponent<Text>(); text.font = font; text.text = value;
            text.fontSize = fontSize; text.alignment = TextAnchor.MiddleCenter; text.color = new Color(.97f,.96f,.86f); text.raycastTarget = false; return text;
        }
        private Button Button(string name, string label, Vector2 anchor, Vector2 pos, Vector2 size, UnityEngine.Events.UnityAction click) {
            var obj = Panel(name, anchor, pos, size, new Color(.16f,.42f,.34f,.96f)); var button = obj.AddComponent<Button>();
            if (click != null) button.onClick.AddListener(click);
            var text = Label(name+" label", label, anchor, pos, size, 27); text.transform.SetParent(obj.transform, true);
            return button;
        }
        private void CreateUI() {
            font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            var root = new GameObject("Studio Canvas", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            canvas = root.GetComponent<Canvas>(); canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = root.GetComponent<CanvasScaler>(); scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = Screen.height>Screen.width ? new Vector2(referenceHeight,referenceWidth) : new Vector2(referenceWidth,referenceHeight); scaler.matchWidthOrHeight = .5f;
            Canvas.ForceUpdateCanvases();
            if (FindFirstObjectByType<EventSystem>() == null) new GameObject("Input events", typeof(EventSystem), typeof(InputSystemUIInputModule));
            if (sceneKind == "menu" || sceneKind == "result") {
                Panel("Title card", new Vector2(.5f,.5f), Vector2.zero, new Vector2(800,440), new Color(.04f,.12f,.16f,.92f));
                Label("Eyebrow", sceneKind == "menu" ? "A LITTLE ADVENTURE AWAITS" : "TRAIL COMPLETE", new Vector2(.5f,.5f), new Vector2(0,155), new Vector2(700,50), 20);
                Label("Game title", title, new Vector2(.5f,.5f), new Vector2(0,75), new Vector2(720,100), 48);
                Label("Subtitle", sceneKind == "result" ? $"You earned {LastScore} points" : subtitle, new Vector2(.5f,.5f), new Vector2(0,-5), new Vector2(710,60), 23);
                Button("Play", sceneKind == "menu" ? playLabel : "Play again", new Vector2(.5f,.5f), new Vector2(0,-100), new Vector2(280,70), Play);
                Label("Keyboard hint", "ARROWS / A D TO MOVE     SPACE TO JUMP", new Vector2(.5f,.5f), new Vector2(0,-175), new Vector2(700,40), 17);
            } else {
                Panel("HUD background", new Vector2(.5f,1), new Vector2(0,-45), new Vector2(1280,90), new Color(.04f,.12f,.16f,.85f));
                scoreText = Label("Score", "COINS 00     SCORE 0000", new Vector2(0,1), new Vector2(285,-45), new Vector2(540,60), 25);
                Button("Pause", "II", new Vector2(1,1), new Vector2(-65,-45), new Vector2(90,60), Pause);
                hint = Label("Hint", "Collect the gold. Follow the sun.", new Vector2(.5f,1), new Vector2(0,-110), new Vector2(800,40), 18);
                foreach (var item in new[] { ("left", "<", new Vector2(90,85), new Vector2(0,0)), ("right", ">", new Vector2(210,85), new Vector2(0,0)), ("jump", "JUMP", new Vector2(-100,85), new Vector2(1,0)) }) {
                    var b = Button(item.Item1, item.Item2, item.Item4, item.Item3, new Vector2(touchButtonSize,touchButtonSize), null);
                    b.gameObject.AddComponent<TouchPad>().action = item.Item1;
                }
                pausePanel = Panel("Pause card", new Vector2(.5f,.5f), Vector2.zero, new Vector2(540,350), new Color(.04f,.12f,.16f,.97f));
                var resume = Button("Resume", "Resume", new Vector2(.5f,.5f), new Vector2(0,80), new Vector2(300,65), Pause);
                var restart = Button("Restart", "Restart", new Vector2(.5f,.5f), Vector2.zero, new Vector2(300,65), Restart);
                var menu = Button("Menu", "Main menu", new Vector2(.5f,.5f), new Vector2(0,-80), new Vector2(300,65), Menu);
                resume.transform.SetParent(pausePanel.transform,true); restart.transform.SetParent(pausePanel.transform,true); menu.transform.SetParent(pausePanel.transform,true);
                pausePanel.SetActive(false);
            }
        }
        void OnDestroy() { Time.timeScale = 1; TouchPad.ResetState(); }
    }
}
