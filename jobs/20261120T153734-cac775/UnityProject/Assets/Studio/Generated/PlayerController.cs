using UnityEngine;
using UnityEngine.InputSystem;
using Studio.Runtime;

[RequireComponent(typeof(Rigidbody2D), typeof(BoxCollider2D))]
public class PlayerController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    public float jumpForce = 10f;
    
    [Header("Input Settings")]
    public bool useKeyboard = true;
    
    private Rigidbody2D rb;
    private bool isGrounded = false;
    private bool moveLeft = false;
    private bool moveRight = false;
    private bool jumpPressed = false;
    
    void Awake()
    {
        rb = GetComponent<Rigidbody2D>();
        if (rb == null)
        {
            Debug.LogError("PlayerController requires a Rigidbody2D on the same GameObject.", this);
            enabled = false;
        }
    }
    
    void Update()
    {
        moveLeft = TouchPad.Left;
        moveRight = TouchPad.Right;
        if (TouchPad.Jump)
        {
            jumpPressed = true;
            TouchPad.Jump = false;
        }
        // Handle keyboard input
        if (useKeyboard)
        {
            HandleKeyboardInput();
        }
        
        // Apply movement
        Vector2 velocity = rb.linearVelocity;
        velocity.x = 0;
        
        if (moveLeft)
            velocity.x = -moveSpeed;
        else if (moveRight)
            velocity.x = moveSpeed;
        
        rb.linearVelocity = velocity;
        
        // Handle jump
        if (jumpPressed && isGrounded)
        {
            rb.AddForce(Vector2.up * jumpForce, ForceMode2D.Impulse);
            jumpPressed = false;
        }
    }
    
    void HandleKeyboardInput()
    {
        var keyboard = Keyboard.current;
        if (keyboard == null) return;
        moveLeft |= keyboard.aKey.isPressed || keyboard.leftArrowKey.isPressed;
        moveRight |= keyboard.dKey.isPressed || keyboard.rightArrowKey.isPressed;
        jumpPressed |= keyboard.spaceKey.isPressed || keyboard.upArrowKey.isPressed;
    }
    
    public void SetMoveLeft(bool value)
    {
        moveLeft = value;
    }
    
    public void SetMoveRight(bool value)
    {
        moveRight = value;
    }
    
    public void SetJump()
    {
        jumpPressed = true;
    }
    
    public void StopMoving()
    {
        moveLeft = false;
        moveRight = false;
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
