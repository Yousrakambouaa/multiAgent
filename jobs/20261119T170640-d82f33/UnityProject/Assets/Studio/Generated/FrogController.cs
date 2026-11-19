using UnityEngine;
using UnityEngine.InputSystem;

public class FrogController : MonoBehaviour
{
    [Header("Movement Settings")]
    public float moveSpeed = 5f;
    public float jumpForce = 10f;
    public LayerMask groundLayer = 1 << 6; // Assuming ground is on layer 6

    private Rigidbody rb;
    private bool isGrounded;
    private Vector2 moveInput;
    private bool jumpInput;
    private Animator animator;

    void Start()
    {
        rb = GetComponent<Rigidbody>();
        animator = GetComponent<Animator>();
    }

    void Update()
    {
        HandleJump();
        UpdateAnimator();
    }

    void FixedUpdate()
    {
        Move();
    }

    public void OnMove(InputValue value)
    {
        moveInput = value.Get<Vector2>();
    }

    public void OnJump()
    {
        jumpInput = true;
    }

    void HandleJump()
    {
        if (jumpInput && isGrounded)
        {
            rb.AddForce(Vector3.up * jumpForce, ForceMode.Impulse);
            isGrounded = false;
        }
        jumpInput = false;
    }

    void Move()
    {
        Vector3 moveDirection = new Vector3(moveInput.x, 0, moveInput.y);
        moveDirection = transform.TransformDirection(moveDirection);
        rb.velocity = new Vector3(moveDirection.x * moveSpeed, rb.velocity.y, moveDirection.z * moveSpeed);
    }

    void UpdateAnimator()
    {
        if (animator != null)
        {
            animator.SetFloat("Speed", moveInput.magnitude);
        }
    }

    void OnCollisionEnter(Collision collision)
    {
        if ((groundLayer.value & (1 << collision.gameObject.layer)) != 0)
        {
            isGrounded = true;
        }
    }
}