using UnityEngine;
using UnityEngine.InputSystem;

public class PlayerController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    public float jumpForce = 10f;
    
    private Rigidbody2D rb;
    private bool isGrounded;
    public bool moveLeft;
    public bool moveRight;
    public bool jump;
    bool mobileLeft;
    bool mobileRight;

    public void SetMobileLeft(bool pressed) { mobileLeft = pressed; moveLeft = pressed; }
    public void SetMobileRight(bool pressed) { mobileRight = pressed; moveRight = pressed; }
    public void RequestJump() { jump = true; }
    
    void Start()
    {
        rb = GetComponent<Rigidbody2D>();
    }
    
    void Update()
    {
        // Handle input
        var keyboard = Keyboard.current;
        bool keyboardLeft = keyboard != null && (keyboard.leftArrowKey.isPressed || keyboard.aKey.isPressed);
        bool keyboardRight = keyboard != null && (keyboard.rightArrowKey.isPressed || keyboard.dKey.isPressed);
        if (keyboardLeft || mobileLeft)
        {
            moveLeft = true;
            moveRight = false;
        }
        else if (keyboardRight || mobileRight)
        {
            moveRight = true;
            moveLeft = false;
        }
        else
        {
            moveLeft = false;
            moveRight = false;
        }
        
        if (keyboard != null && (keyboard.spaceKey.wasPressedThisFrame || keyboard.upArrowKey.wasPressedThisFrame) && isGrounded)
        {
            jump = true;
        }
        
        // Mobile input
        if (Application.platform == RuntimePlatform.Android || Application.platform == RuntimePlatform.IPhonePlayer)
        {
            // Touch input handled by UI buttons
        }
    }
    
    void FixedUpdate()
    {
        // Movement
        if (rb == null) return;
        Vector2 velocity = rb.linearVelocity;
        
        if (moveLeft)
        {
            velocity.x = -moveSpeed;
        }
        else if (moveRight)
        {
            velocity.x = moveSpeed;
        }
        else
        {
            velocity.x = 0;
        }
        
        // Jump
        if (jump && isGrounded)
        {
            velocity.y = jumpForce;
            jump = false;
        }
        
        rb.linearVelocity = velocity;
    }
    
    void OnCollisionEnter2D(Collision2D collision)
    {
        if (collision.gameObject.CompareTag("Ground"))
        {
            isGrounded = true;
        }
    }
    
    void OnCollisionExit2D(Collision2D collision)
    {
        if (collision.gameObject.CompareTag("Ground"))
        {
            isGrounded = false;
        }
    }
}
