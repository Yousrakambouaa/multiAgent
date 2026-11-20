using UnityEngine;
using UnityEngine.InputSystem;

public class PlayerController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    public float jumpForce = 10f;
    
    [Header("Components")]
    private Rigidbody2D rb;
    private BoxCollider2D boxCollider;
    
    [Header("Input")]
    private Vector2 moveInput;
    private bool jumpPressed;
    
    [Header("State")]
    private bool isGrounded;
    
    void Start()
    {
        rb = GetComponent<Rigidbody2D>();
        boxCollider = GetComponent<BoxCollider2D>();
        
        // Set gravity scale to make jumping feel right
        rb.gravityScale = 3f;
    }
    
    void Update()
    {
        // Handle input
        if (Keyboard.current != null)
        {
            moveInput.x = Keyboard.current.dKey.isPressed || Keyboard.current.rightArrowKey.isPressed ? 1f : 
                         Keyboard.current.aKey.isPressed || Keyboard.current.leftArrowKey.isPressed ? -1f : 0f;
            jumpPressed = Keyboard.current.spaceKey.isPressed || Keyboard.current.upArrowKey.isPressed;
        }
        
        // Check if grounded
        CheckGrounded();
        
        // Jump if pressed and grounded
        if (jumpPressed && isGrounded)
        {
            rb.linearVelocity = new Vector2(rb.linearVelocity.x, 0f); // Stop upward velocity
            rb.AddForce(Vector2.up * jumpForce, ForceMode2D.Impulse);
            isGrounded = false;
        }
    }
    
    void FixedUpdate()
    {
        // Apply horizontal movement
        rb.linearVelocity = new Vector2(moveInput.x * moveSpeed, rb.linearVelocity.y);
    }
    
    void CheckGrounded()
    {
        // Simple grounded check using raycast
        Vector2 rayStart = transform.position + new Vector3(0, -boxCollider.bounds.extents.y, 0);
        RaycastHit2D hit = Physics2D.Raycast(rayStart, Vector2.down, 0.1f, LayerMask.GetMask("Ground"));
        
        isGrounded = hit.collider != null;
    }
    
    // Called by coin collector when a coin is collected
    public void OnCoinCollected()
    {
        // This method can be extended if needed
    }
    
    // Expose move input for mobile controls
    public Vector2 GetMoveInput()
    {
        return moveInput;
    }
    
    // Expose jump state for mobile controls
    public bool GetJumpPressed()
    {
        return jumpPressed;
    }
    
    // Set move input for mobile controls
    public void SetMoveInput(Vector2 input)
    {
        moveInput = input;
    }
    
    // Set jump state for mobile controls
    public void SetJumpPressed(bool pressed)
    {
        jumpPressed = pressed;
    }
}