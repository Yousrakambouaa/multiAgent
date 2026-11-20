using UnityEngine;
using UnityEngine.InputSystem;

public class PlayerController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    public float jumpForce = 10f;
    
    [Header("Input Settings")]
    public bool useKeyboard = true;
    
    private Rigidbody2D rb;
    private bool isGrounded;
    private Vector2 moveInput;
    private bool jumpPressed;
    
    void Start()
    {
        rb = GetComponent<Rigidbody2D>();
        // Set gravity scale to make jumping feel right
        if (rb != null)
        {
            rb.gravityScale = 3f;
        }
    }
    
    void Update()
    {
        if (useKeyboard)
        {
            HandleKeyboardInput();
        }
        else
        {
            HandleTouchInput();
        }
        
        // Apply movement
        if (rb != null)
        {
            rb.linearVelocity = new Vector2(moveInput.x * moveSpeed, rb.linearVelocity.y);
        }
        
        // Jump if grounded and jump pressed
        if (isGrounded && jumpPressed)
        {
            if (rb != null)
            {
                rb.AddForce(Vector2.up * jumpForce, ForceMode2D.Impulse);
            }
            jumpPressed = false;
        }
    }
    
    void HandleKeyboardInput()
    {
        bool dPressed = Keyboard.current?.dKey.isPressed ?? false;
        bool rightPressed = Keyboard.current?.rightArrowKey.isPressed ?? false;
        bool aPressed = Keyboard.current?.aKey.isPressed ?? false;
        bool leftPressed = Keyboard.current?.leftArrowKey.isPressed ?? false;
        
        moveInput.x = (dPressed || rightPressed) ? 1f : (aPressed || leftPressed ? -1f : 0f);
        
        jumpPressed = Keyboard.current?.spaceKey.isPressed ?? false;
    }
    
    void HandleTouchInput()
    {
        // Movement handled by touch buttons
        // Jump handled by jump button
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
    
    public void MoveLeft()
    {
        moveInput.x = -1f;
    }
    
    public void MoveRight()
    {
        moveInput.x = 1f;
    }
    
    public void StopMoving()
    {
        moveInput.x = 0f;
    }
    
    public void Jump()
    {
        jumpPressed = true;
    }
}