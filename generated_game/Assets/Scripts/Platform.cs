using UnityEngine;

public class Platform : MonoBehaviour
{
    public float speed;
    public Vector3 startPoint;
    public Vector3 endPoint;
    private Vector3 target;

    void Start() 
    {
        target = endPoint;
    }

    void Update() 
    {
        MovePlatform();
    }

    void MovePlatform() 
    {
        transform.position = Vector3.MoveTowards(transform.position, target, speed * Time.deltaTime);
        if (transform.position == endPoint) 
        {
            target = startPoint;
        }
        else if (transform.position == startPoint)
        {
            target = endPoint;
        }
    }
}