using UnityEngine;
using UnityEngine.InputSystem;

public class PlayerController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    public float jumpForce = 10f;
    
    private Rigidbody2D rb;
    private bool isGrounded;
    private float moveInput;
    private bool jumpRequested;
    
    void Start()
    {
        rb = GetComponent<Rigidbody2D>();
        if (rb == null) Debug.LogError("Rigidbody2D missing on Player");
    }
    
    void Update()
    {
        moveInput = 0f;
        if (Keyboard.current != null)
        {
            if (Keyboard.current.leftArrowKey.isPressed || Keyboard.current.aKey.isPressed) moveInput = -1f;
            else if (Keyboard.current.rightArrowKey.isPressed || Keyboard.current.dKey.isPressed) moveInput = 1f;
            
            if ((Keyboard.current.spaceKey.isPressed || Keyboard.current.upArrowKey.isPressed) && isGrounded)
            {
                jumpRequested = true;
            }
        }
    }
    
    void FixedUpdate()
    {
        if (rb != null)
        {
            rb.velocity = new Vector2(moveInput * moveSpeed, rb.velocity.y);
            
            if (jumpRequested && isGrounded)
            {
                rb.AddForce(Vector2.up * jumpForce, ForceMode2D.Impulse);
                isGrounded = false;
                jumpRequested = false;
            }
        }
    }
    
    voidCollisionEnter2D(Collision2D collision)
    {
        if (collision prefab != null && collision prefab CompareTag("Ground"))
        {
            isGrounded = true;
        }
    }
    
    // Mobile button handlers
    public void OnMoveLeft() { moveInput = -1f; }
    public void OnMoveRight() { moveInput = 1f; }
    public void OnStopMoving() { moveInput = 0f; }
    public void OnJump() { if (isGrounded) jumpRequested = true; }
}