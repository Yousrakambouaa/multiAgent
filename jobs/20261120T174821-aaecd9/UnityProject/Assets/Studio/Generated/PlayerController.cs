using UnityEngine;
using UnityEngine.InputSystem;

public class PlayerController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    
    private Rigidbody2D rb;
    private Vector2 movementInput;
    
    void Awake()
    {
        // Cache the Rigidbody2D component
        rb = GetComponent<Rigidbody2D>();
        if (rb == null)
        {
            Debug.LogError("Rigidbody2D component not found on player object.");
        }
    }
    
    void Update()
    {
        // Read input
        if (Keyboard.current != null)
        {
            movementInput.x = Keyboard.current.dKey.isPressed || Keyboard.current.rightArrowKey.isPressed ? 1f : 
                             Keyboard.current.aKey.isPressed || Keyboard.current.leftArrowKey.isPressed ? -1f : 0f;
            movementInput.y = Keyboard.current.wKey.isPressed || Keyboard.current.upArrowKey.isPressed ? 1f : 
                             Keyboard.current.sKey.isPressed || Keyboard.current.downArrowKey.isPressed ? -1f : 0f;
        }
    }
    
    void FixedUpdate()
    {
        // Apply movement
        if (rb != null)
        {
            rb.linearVelocity = movementInput * moveSpeed;
        }
    }
}