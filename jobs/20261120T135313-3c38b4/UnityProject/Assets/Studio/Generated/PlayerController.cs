using UnityEngine;
using UnityEngine.InputSystem;

public class PlayerController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    public float jumpForce = 10f;
    
    [Header("Input Settings")]
    public bool isMobile = false;
    
    private Rigidbody2D rb;
    private bool isGrounded;
    private bool moveLeft;
    private bool moveRight;
    private bool jump;
    private bool canJump = true;
    
    [Header("UI")]
    public UnityEngine.UI.Text scoreText;
    public UnityEngine.UI.Text winText;
    
    private int score = 0;
    private const int TOTAL_COINS = 5;
    
    void Start()
    {
        rb = GetComponent<Rigidbody2D>();
        winText.gameObject.SetActive(false);
    }
    
    void Update()
    {
        // Handle input
        if (isMobile)
        {
            // Mobile input handled by UI buttons
        }
        else
        {
            // Keyboard input
            moveLeft = Keyboard.current.aKey.isPressed || Keyboard.current.leftArrowKey.isPressed;
            moveRight = Keyboard.current.dKey.isPressed || Keyboard.current.rightArrowKey.isPressed;
            jump = Keyboard.current.spaceKey.isPressed || Keyboard.current.upArrowKey.isPressed;
        }
        
        // Jump if grounded and jump key pressed
        if (jump && canJump && isGrounded)
        {
            rb.AddForce(Vector2.up * jumpForce, ForceMode2D.Impulse);
            canJump = false;
        }
    }
    
    void FixedUpdate()
    {
        // Horizontal movement
        float moveX = 0;
        if (moveLeft) moveX = -moveSpeed;
        else if (moveRight) moveX = moveSpeed;
        
        rb.velocity = new Vector2(moveX, rb.velocity.y);
    }
    
    void OnCollisionEnter2D(Collision2D collision)
    {
        if (collision.gameObject.CompareTag("Ground"))
        {
            isGrounded = true;
            canJump = true;
        }
    }
    
    void OnCollisionExit2D(Collision2D collision)
    {
        if (collision.gameObject.CompareTag("Ground"))
        {
            isGrounded = false;
        }
    }
    
    void OnTriggerEnter2D(Collider2D other)
    {
        if (other.CompareTag("Coin"))
        {
            // Increase score
            score++;
            scoreText.text = "Score: " + score;
            
            // Destroy coin
            Destroy(other.gameObject);
            
            // Check win condition
            if (score >= TOTAL_COINS)
            {
                winText.text = "YOU WIN!";
                winText.gameObject.SetActive(true);
            }
        }
    }
    
    // Mobile input methods
    public void OnMoveLeft()
    {
        moveLeft = true;
    }
    
    public void OnMoveLeftRelease()
    {
        moveLeft = false;
    }
    
    public void OnMoveRight()
    {
        moveRight = true;
    }
    
    public void OnMoveRightRelease()
    {
        moveRight = false;
    }
    
    public void OnJump()
    {
        jump = true;
    }
    
    public void OnJumpRelease()
    {
        jump = false;
    }
}